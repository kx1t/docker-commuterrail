#!/usr/bin/env python3
import datetime
import json
import mimetypes
import os
import socket
import threading
import time
from ipaddress import ip_address
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

PRIM_API = 'https://prim.iledefrance-mobilites.fr/marketplace'
MBTA_API = 'https://api-v3.mbta.com'
IDFM_DATA_API = 'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/arrets-lignes/records'
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '60'))
PORT = int(os.getenv('PORT', '80'))
PRIM_API_KEY = os.getenv('PRIM_API_KEY', '')
MBTA_API_KEY = os.getenv('MBTA_API_KEY', '') or os.getenv('BOSTON_API_KEY', '') or os.getenv('API_KEY', '')
MAX_THREADS = int(os.getenv('HTTP_WORKERS', '10'))
DEFAULT_PARIS_PATH = 'estimated-timetable?LineRef=STIF:Line::C01379:'
STATIC_ROOT = Path('/app')

cache_lock = threading.RLock()
cache_store = {}
transit_last_requested = {}
cache_stats = {'clients': {}, 'transits': {}}
DATA_DIR = Path(os.getenv('DATA_DIR', '/data'))
STATS_FILE = DATA_DIR / 'cache_stats.json'


def ensure_stats_path():
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def sanitize_private_ip(raw_ip: str | None) -> tuple[str | None, bool]:
    candidate = (raw_ip or '').strip().split(',')[0].strip()
    if not candidate or candidate in ('unknown', '-', 'None', 'null'):
        return None, True
    try:
        parsed = ip_address(candidate)
    except ValueError:
        return candidate, False
    private = parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_reserved
    if private:
        return None, True
    return str(parsed), False


def record_stats_event(event_type: str, transit: str, endpoint: str, query: dict | None = None, client_ip: str | None = None, user_agent: str | None = None):
    ensure_stats_path()
    if not transit:
        return
    now = current_timestamp()
    with cache_lock:
        entry_key = read_cache_key(transit, endpoint, query or {})
        transit_bucket = cache_stats.setdefault('transits', {}).setdefault(normalize_key(transit), {})
        entry = transit_bucket.setdefault(entry_key, {
            'endpoint': endpoint,
            'query': query or {},
            'hits': [],
            'misses': [],
            'refreshes': [],
            'requests': [],
            'last_access': None,
            'last_hit': None,
            'last_miss': None,
            'last_refresh': None,
        })
        entry['endpoint'] = endpoint
        entry['query'] = query or {}
        entry['requests'].append(now)
        entry['last_access'] = now
        if event_type == 'hit':
            entry['hits'].append(now)
            entry['last_hit'] = now
        elif event_type == 'miss':
            entry['misses'].append(now)
            entry['last_miss'] = now
        elif event_type == 'refresh':
            entry['refreshes'].append(now)
            entry['last_refresh'] = now
        cutoff = now - 86400
        for key in ('hits', 'misses', 'refreshes', 'requests'):
            entry[key] = [ts for ts in (entry.get(key) or []) if float(ts) >= cutoff]
        client_key = 'anonymous'
        if client_ip or user_agent:
            client_key = f"{(client_ip or 'unknown')[:64]}|{(user_agent or 'unknown')[:128]}"
        if client_ip or user_agent:
            client_bucket = cache_stats.setdefault('clients', {}).setdefault(client_key, {
                'first_seen': now,
                'last_seen': now,
                'requests': [],
                'ip': None,
                'user_agent': user_agent or 'unknown',
                'internal_ip': True,
            })
            client_bucket['last_seen'] = now
            client_bucket['requests'].append(now)
            client_bucket['user_agent'] = user_agent or client_bucket.get('user_agent', 'unknown')
            safe_ip, internal = sanitize_private_ip(client_ip)
            if safe_ip:
                client_bucket['ip'] = safe_ip
                client_bucket['internal_ip'] = False
            else:
                client_bucket['ip'] = None
                client_bucket['internal_ip'] = True
            if client_bucket.get('first_seen') is None:
                client_bucket['first_seen'] = now
            cutoff = now - 86400
            client_bucket['requests'] = [ts for ts in (client_bucket.get('requests') or []) if float(ts) >= cutoff]
        try:
            STATS_FILE.write_text(json.dumps(cache_stats, sort_keys=True))
        except OSError:
            pass


class ConcurrencyLimitedHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=10):
        super().__init__(server_address, RequestHandlerClass)
        self._semaphore = threading.Semaphore(max_workers)

    def get_semaphore(self):
        return self._semaphore


def normalize_key(value: str) -> str:
    return (value or '').strip() or 'default'


def current_timestamp() -> float:
    return time.time()


def format_cache_time(timestamp: float) -> str:
    return datetime.datetime.fromtimestamp(timestamp, tz=datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')


def parse_json_query(raw):
    if raw is None or raw == '':
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def make_headers(transit: str) -> dict:
    if transit.lower() == 'paris':
        return {'apikey': PRIM_API_KEY} if PRIM_API_KEY else {}
    if transit.lower() == 'boston':
        return {'x-api-key': MBTA_API_KEY} if MBTA_API_KEY else {}
    return {}


def read_cache_key(transit: str, endpoint: str, query: dict | None = None) -> str:
    return f"{normalize_key(transit)}::{normalize_key(endpoint)}::{json.dumps(query or {}, sort_keys=True)}"


def mark_transit_requested(transit: str):
    with cache_lock:
        transit_last_requested[normalize_key(transit)] = current_timestamp()


def transit_requested_recently(transit: str) -> bool:
    with cache_lock:
        last_time = transit_last_requested.get(normalize_key(transit))
        if last_time is None:
            return False
        return (current_timestamp() - last_time) <= CACHE_TTL_SECONDS


def normalize_api_endpoint(value: str | None) -> str:
    candidate = (value or '').strip()
    if not candidate:
        return 'unknown'
    parsed = urlparse(candidate)
    path = parsed.path or candidate
    if not path.startswith('/'):
        path = '/' + path
    return path or '/'


def count_recent(events, seconds: int):
    if not events:
        return 0
    cutoff = current_timestamp() - seconds
    return sum(1 for ts in events if float(ts) >= cutoff)


def load_cache_stats():
    ensure_stats_path()
    if not STATS_FILE.exists():
        return {'clients': {}, 'transits': {}}
    try:
        loaded = json.loads(STATS_FILE.read_text())
        if isinstance(loaded, dict):
            return loaded
    except (OSError, ValueError):
        pass
    return {'clients': {}, 'transits': {}}


cache_stats.update(load_cache_stats())


def http_error_text(exc):
    status = getattr(exc, 'code', None) or getattr(exc, 'status', None)
    reason = getattr(exc, 'reason', None) or str(exc)
    if status is not None:
        return f'Server error {status}: {reason}'
    if reason and reason != 'None':
        return f'Server error: {reason}'
    return 'Server error'


def warning_for_stale_cache(stale_entry, exc):
    updated_at = stale_entry.get('fetched_at') or stale_entry.get('expires_at') or current_timestamp()
    return f"Warning: Transit data was last updated on {format_cache_time(updated_at)}. {http_error_text(exc)}"


def error_for_missing_cache(exc):
    return f"Error: transit data cannot be retrieved. {http_error_text(exc)}"


def upstream_url_for(transit: str, endpoint: str, query: dict | None = None):
    base = PRIM_API if transit.lower() == 'paris' else MBTA_API
    if transit.lower() == 'paris':
        if endpoint in ('/stops', 'stops'):
            line = (query or {}).get('route', '')
            line_number = str(line).split('-')[-1]
            return f"{IDFM_DATA_API}?{urlencode({'where': f'route_long_name=\"{line_number}\"', 'limit': '100'})}"
        if endpoint.startswith('/stop-monitoring') or endpoint.startswith('stop-monitoring'):
            return f"{base}/{endpoint.lstrip('/')}"
        return f"{base}/{endpoint.lstrip('/')}"
    path = endpoint if endpoint.startswith('/') else f'/{endpoint}'
    params = urlencode(query or {}, doseq=True)
    suffix = f'?{params}' if params else ''
    return f"{base}{path}{suffix}"


def fetch_upstream(transit: str, endpoint: str, query: dict | None = None):
    if not endpoint:
        raise ValueError('empty API endpoint')
    url = upstream_url_for(transit, endpoint, query)
    request = Request(url, headers=make_headers(transit))
    with urlopen(request, timeout=20) as response:
        payload = response.read()
        if not payload:
            raise RuntimeError('Empty upstream response')
        return json.loads(payload.decode('utf-8'))


def fetch_and_cache(transit: str, endpoint: str, query: dict | None = None, client_ip: str | None = None, user_agent: str | None = None):
    data = fetch_upstream(transit, endpoint, query)
    key = read_cache_key(transit, endpoint, query)
    fetched_at = current_timestamp()
    with cache_lock:
        cache_store[key] = {
            'data': data,
            'expires_at': fetched_at + CACHE_TTL_SECONDS,
            'fetched_at': fetched_at,
        }
    record_stats_event('refresh', transit, endpoint, query, client_ip=client_ip, user_agent=user_agent)
    return cache_store[key]


def get_cached_payload(transit: str, endpoint: str, query: dict | None = None):
    key = read_cache_key(transit, endpoint, query)
    with cache_lock:
        cached = cache_store.get(key)
        if cached and cached['expires_at'] > current_timestamp():
            return cached
        if cached:
            return cached
    return None


def build_stats_payload():
    now = current_timestamp()
    snapshot = {'generated_at': now, 'clients': [], 'transits': []}
    with cache_lock:
        for client_key, client in sorted((cache_stats.get('clients') or {}).items(), key=lambda item: item[1].get('last_seen', 0), reverse=True):
            ip_value = client.get('ip')
            if not ip_value and client.get('internal_ip'):
                ip_value = 'private IP suppressed'
            snapshot['clients'].append({
                'client': client_key,
                'ip': ip_value,
                'internal_ip': bool(client.get('internal_ip')),
                'user_agent': client.get('user_agent') or 'unknown',
                'requests_last_minute': count_recent(client.get('requests') or [], 60),
                'requests_last_hour': count_recent(client.get('requests') or [], 3600),
                'requests_last_day': count_recent(client.get('requests') or [], 86400),
                'first_seen': client.get('first_seen'),
                'last_seen': client.get('last_seen'),
            })

        by_api_endpoint = {}
        for transit_name, transit_bucket in sorted((cache_stats.get('transits') or {}).items()):
            for key, entry in (transit_bucket or {}).items():
                endpoint = normalize_api_endpoint(entry.get('endpoint'))
                bucket = by_api_endpoint.setdefault((transit_name, endpoint), {
                    'transit': transit_name,
                    'endpoint': endpoint,
                    'query': {},
                    'last_access': None,
                    'last_hit': None,
                    'last_miss': None,
                    'last_refresh': None,
                    'hits': [],
                    'misses': [],
                    'refreshes': [],
                    'requests': [],
                })
                for field in ('hits', 'misses', 'refreshes', 'requests'):
                    bucket[field].extend(entry.get(field) or [])
                for field in ('last_access', 'last_hit', 'last_miss', 'last_refresh'):
                    value = entry.get(field)
                    if value is not None:
                        current_value = bucket.get(field)
                        if current_value is None or float(value) > float(current_value):
                            bucket[field] = value

        for (transit_name, endpoint), bucket in sorted(by_api_endpoint.items(), key=lambda item: (item[0][0], item[1].get('last_access') or 0), reverse=True):
            hits = bucket.get('hits') or []
            misses = bucket.get('misses') or []
            refreshes = bucket.get('refreshes') or []
            requests = bucket.get('requests') or []
            snapshot['transits'].append({
                'transit': transit_name,
                'cache_key': f"{transit_name}::{endpoint}",
                'endpoint': endpoint,
                'query': {},
                'last_access': bucket.get('last_access'),
                'last_hit': bucket.get('last_hit'),
                'last_miss': bucket.get('last_miss'),
                'last_refresh': bucket.get('last_refresh'),
                'hits_last_minute': count_recent(hits, 60),
                'hits_last_hour': count_recent(hits, 3600),
                'hits_last_day': count_recent(hits, 86400),
                'misses_last_minute': count_recent(misses, 60),
                'misses_last_hour': count_recent(misses, 3600),
                'misses_last_day': count_recent(misses, 86400),
                'refreshes_last_minute': count_recent(refreshes, 60),
                'refreshes_last_hour': count_recent(refreshes, 3600),
                'refreshes_last_day': count_recent(refreshes, 86400),
                'requests_last_minute': count_recent(requests, 60),
                'requests_last_hour': count_recent(requests, 3600),
                'requests_last_day': count_recent(requests, 86400),
            })
    return snapshot


def resolve_endpoint(path: str, transit: str):
    path = (path or '').strip()
    if not path or path == '/':
        return DEFAULT_PARIS_PATH if transit.lower() == 'paris' else '/routes'
    return path.lstrip('/')


def strip_proxy_prefix(path: str) -> str:
    normalized = (path or '/').strip()
    if not normalized.startswith('/'):
        normalized = '/' + normalized
    if normalized.startswith('/commuterrail'):
        normalized = normalized[len('/commuterrail'):] or '/'
    return normalized or '/'


def build_json_response(transit: str, endpoint: str, payload=None, status='ok', message=None, stale=False):
    response = {
        'transit': transit,
        'endpoint': endpoint,
        'cached': not stale,
        'status': status,
        'data': payload or {},
    }
    if message:
        response['message'] = message
    return response


def serve_static_file(self, relative_path: str):
    candidate = (STATIC_ROOT / relative_path.lstrip('/')).resolve()
    if not str(candidate).startswith(str(STATIC_ROOT.resolve())):
        candidate = STATIC_ROOT / 'index.html'
    if candidate.is_dir():
        candidate = STATIC_ROOT / 'index.html'
    if not candidate.exists():
        candidate = STATIC_ROOT / 'index.html'
    data = candidate.read_bytes()
    mime_type = mimetypes.guess_type(str(candidate))[0] or 'application/octet-stream'
    self.send_response(200)
    self.send_header('Content-Type', mime_type)
    self.end_headers()
    self.wfile.write(data)


class TransitCacheHandler(BaseHTTPRequestHandler):
    server_version = 'TransitCache/1.0'

    def do_GET(self):
        self.server.get_semaphore().acquire()
        try:
            parsed = urlparse(self.path)
            request_path = strip_proxy_prefix(parsed.path)
            query = parse_qs(parsed.query)
            transit = (query.get('transit', ['paris'])[0] or 'paris').lower()
            endpoint = query.get('path', [resolve_endpoint(request_path, transit)])[0]
            request_query = parse_json_query(query.get('query', ['{}'])[0])
            mark_transit_requested(transit)

            if request_path in ('/healthz', '/health'):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'transit': transit}).encode())
                return

            if request_path in ('/api/stats', '/stats'):
                payload = build_stats_payload()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'data': payload}).encode('utf-8'))
                return

            if request_path in ('/api/cache', '/cache') or request_path.startswith('/api/cache/'):
                try:
                    client_ip = (self.headers.get('X-Forwarded-For') or self.headers.get('X-Real-IP') or self.client_address[0] if hasattr(self, 'client_address') else None)
                    client_ip = client_ip.split(',')[0].strip() if isinstance(client_ip, str) and client_ip else client_ip
                    user_agent = self.headers.get('User-Agent')
                    cached = get_cached_payload(transit, endpoint, request_query)
                    should_refresh = cached is None or cached.get('expires_at', 0) <= current_timestamp()
                    if cached is not None:
                        record_stats_event('hit', transit, endpoint, request_query, client_ip=client_ip, user_agent=user_agent)
                    else:
                        record_stats_event('miss', transit, endpoint, request_query, client_ip=client_ip, user_agent=user_agent)
                    if should_refresh and transit_requested_recently(transit):
                        cached = fetch_and_cache(transit, endpoint, request_query, client_ip=client_ip, user_agent=user_agent)
                    if cached is not None:
                        stale = cached.get('expires_at', 0) <= current_timestamp()
                        body = build_json_response(
                            transit,
                            endpoint,
                            payload=cached['data'],
                            status='warning' if stale else 'ok',
                            message=None if not stale else f"Warning: Transit data was last updated on {format_cache_time(cached.get('fetched_at') or cached.get('expires_at') or current_timestamp())}. Data is stale.",
                            stale=stale,
                        )
                        self.send_response(200)
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(body).encode('utf-8'))
                        return
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(build_json_response(transit, endpoint, status='error', message='Error: transit data cannot be retrieved. No recent refresh window is available.')).encode('utf-8'))
                    return
                except Exception as exc:
                    stale = get_cached_payload(transit, endpoint, request_query)
                    if stale:
                        body = build_json_response(
                            transit,
                            endpoint,
                            payload=stale['data'],
                            status='warning',
                            message=warning_for_stale_cache(stale, exc),
                            stale=True,
                        )
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(body).encode('utf-8'))
                        return
                    body = build_json_response(
                        transit,
                        endpoint,
                        status='error',
                        message=error_for_missing_cache(exc),
                    )
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(body).encode('utf-8'))
                    return

            if request_path in ('/', '/index.html'):
                serve_static_file(self, '/index.html')
                return

            if request_path.startswith('/'):
                serve_static_file(self, request_path)
                return

            self.send_response(404)
            self.end_headers()
        finally:
            self.server.get_semaphore().release()

    def log_message(self, *args):
        return


if __name__ == '__main__':
    httpd = ConcurrencyLimitedHTTPServer(('', PORT), TransitCacheHandler, max_workers=MAX_THREADS)
    httpd.daemon_threads = True
    httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print(f'Serving on port {PORT} with concurrency limit {MAX_THREADS}')
    httpd.serve_forever()
