import time

from app import server


def test_build_stats_payload_includes_cached_hostname_for_client():
    server.cache_stats.clear()
    server.hostname_cache.clear()
    server.hostname_cache['8.8.8.8'] = {
        'name': 'dns.google',
        'resolved_at': time.time(),
        'last_attempted': time.time(),
        'not_found': False,
    }
    server.cache_stats['clients'] = {
        '8.8.8.8|test-agent': {
            'first_seen': time.time(),
            'last_seen': time.time(),
            'requests': [time.time()],
            'ip': '8.8.8.8',
            'user_agent': 'test-agent',
            'internal_ip': False,
        }
    }

    payload = server.build_stats_payload()

    assert payload['clients'][0]['name'] == 'dns.google'
    assert payload['clients'][0]['ip'] == '8.8.8.8'
