#!/usr/bin/env python3
import json
import mimetypes
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

PRIM_API = 'https://prim.iledefrance-mobilites.fr/marketplace'
CACHE_TTL_SECONDS = int(os.getenv('CACHE_TTL_SECONDS', '60'))
PORT = int(os.getenv('PORT', '8000'))
PRIM_API_KEY = os.getenv('PRIM_API_KEY', '')
MAX_THREADS = int(os.getenv('HTTP_WORKERS', '10'))
DEFAULT_PARIS_PATH = 'estimated-timetable?LineRef=STIF:Line::C01379:'
STATIC_ROOT = Path('/app')

cache_lock = threading.RLock()
cache_store = {}


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


def make_headers() -> dict:
    headers = {}
    if PRIM_API_KEY:
        headers['apikey'] = PRIM_API_KEY
    return headers


def read_cache_key(transit: str, endpoint: str) -> str:
    return f"{normalize_key(transit)}::{normalize_key(endpoint)}"


def fetch_pri_payload(endpoint: str):
    if not endpoint:
        raise ValueError('empty PRIM endpoint')
    url = f"{PRIM_API}/{endpoint.lstrip('/')}"
    request = Request(url, headers=make_headers())
    with urlopen(request, timeout=20) as response:
        payload = response.read()
        if not payload:
            raise RuntimeError('Empty PRIM response')
        return json.loads(payload.decode('utf-8'))


def fetch_and_cache(transit: str, endpoint: str):
    data = fetch_pri_payload(endpoint)
    key = read_cache_key(transit, endpoint)
    with cache_lock:
        cache_store[key] = {
            'data': data,
            'expires_at': current_timestamp() + CACHE_TTL_SECONDS,
            'fetched_at': current_timestamp(),
        }
    return cache_store[key]


def get_cached_payload(transit: str, endpoint: str):
    key = read_cache_key(transit, endpoint)
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
        return DEFAULT_PARIS_PATH if transit.lower() == 'paris' else ''
    return path.lstrip('/')


def build_json_response(transit: str, endpoint: str, payload=None, error=None, stale=False):
    payload = payload or {}
    response = {
        'transit': transit,
        'endpoint': endpoint,
        'cached': not stale,
        'status': 'error' if error else 'ok',
        'data': payload,
    }
    if error:
        response['error'] = error
    return response


def refresh_known_endpoints():
    defaults = {
        'paris': [DEFAULT_PARIS_PATH],
        'boston': [],
    }
    for transit, endpoints in defaults.items():
        for endpoint in endpoints:
            try:
                fetch_and_cache(transit, endpoint)
            except Exception:
                pass


def refresh_loop():
    while True:
        try:
            refresh_known_endpoints()
        except Exception:
            pass
        time.sleep(CACHE_TTL_SECONDS)


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

            if parsed.path in ('/healthz', '/health'):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'transit': transit}).encode())
                return

            if parsed.path in ('/api/cache', '/cache') or parsed.path.startswith('/api/cache/'):
                try:
                    cached = get_cached_payload(transit, endpoint)
                    if cached is None:
                        cached = fetch_and_cache(transit, endpoint)
                    self.send_response(200)
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    body = build_json_response(transit, endpoint, payload=cached['data'], stale=(cached.get('expires_at', 0) <= current_timestamp()))
                    self.wfile.write(json.dumps(body).encode('utf-8'))
                except Exception as exc:
                    stale = get_cached_payload(transit, endpoint)
                    if stale:
                        body = build_json_response(transit, endpoint, payload=stale['data'], stale=True)
                    else:
                        body = build_json_response(transit, endpoint, error=str(exc))
                    self.send_response(200 if stale else 502)
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
    threading.Thread(target=refresh_loop, daemon=True).start()
    httpd = ConcurrencyLimitedHTTPServer(('', PORT), TransitCacheHandler, max_workers=MAX_THREADS)
    httpd.daemon_threads = True
    httpd.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    print(f'Serving on port {PORT} with concurrency limit {MAX_THREADS}')
    httpd.serve_forever()
