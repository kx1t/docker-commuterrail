#!/usr/bin/env python3
import datetime
import json
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

PRIM_API = 'https://prim.iledefrance-mobilites.fr/marketplace'
MBTA_API = 'https://api-v3.mbta.com'
IDFM_DATA_API = 'https://data.iledefrance-mobilites.fr/api/explore/v2.1/catalog/datasets/arrets-lignes/records'
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '60'))
PORT = int(os.getenv('PORT', '8000'))
PRIM_API_KEY = os.getenv('PRIM_API_KEY', '')
MBTA_API_KEY = os.getenv('MBTA_API_KEY', '') or os.getenv('BOSTON_API_KEY', '') or os.getenv('API_KEY', '')
MAX_THREADS = int(os.getenv('HTTP_WORKERS', '10'))
DEFAULT_PARIS_PATH = 'estimated-timetable?LineRef=STIF:Line::C01379:'
STATIC_ROOT = Path('/app')

cache_lock = threading.RLock()
cache_store = {}
transit_last_requested = {}


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


def fetch_and_cache(transit: str, endpoint: str, query: dict | None = None):
    data = fetch_upstream(transit, endpoint, query)
    key = read_cache_key(transit, endpoint, query)
    with cache_lock:
        cache_store[key] = {
            'data': data,
            'expires_at': current_timestamp() + CACHE_TTL_SECONDS,
            'fetched_at': current_timestamp(),
        }
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


def resolve_endpoint(path: str, transit: str):
    path = (path or '').strip()
    if not path or path == '/':
        return DEFAULT_PARIS_PATH if transit.lower() == 'paris' else '/routes'
    return path.lstrip('/')


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
            query = parse_qs(parsed.query)
            transit = (query.get('transit', ['paris'])[0] or 'paris').lower()
            endpoint = query.get('path', [resolve_endpoint(parsed.path, transit)])[0]
            request_query = parse_json_query(query.get('query', ['{}'])[0])
            mark_transit_requested(transit)

            if parsed.path in ('/healthz', '/health'):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'transit': transit}).encode())
                return

            if parsed.path in ('/api/cache', '/cache') or parsed.path.startswith('/api/cache/'):
                try:
                    cached = get_cached_payload(transit, endpoint, request_query)
                    should_refresh = cached is None or cached.get('expires_at', 0) <= current_timestamp()
                    if should_refresh and transit_requested_recently(transit):
                        cached = fetch_and_cache(transit, endpoint, request_query)
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

            if parsed.path in ('/', '/index.html', '/commuterrail', '/commuterrail/') or parsed.path.startswith('/commuterrail'):
                serve_static_file(self, '/index.html')
                return

            if parsed.path.startswith('/'):
                serve_static_file(self, parsed.path)
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
