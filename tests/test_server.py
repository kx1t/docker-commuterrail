from urllib.parse import parse_qs, urlparse

from app.server import (
    get_client_ip_from_headers,
    strip_proxy_prefix,
    upstream_url_for,
    warning_for_stale_cache,
)


def test_strip_proxy_prefix_removes_proxy_path():
    assert strip_proxy_prefix('/commuterrail/?transit=paris') == '/?transit=paris'
    assert strip_proxy_prefix('/api/cache?transit=boston') == '/api/cache?transit=boston'


def test_paris_stops_url_builds_expected_query():
    url = upstream_url_for('paris', '/stops', {'route': 'metro-9'})
    parsed = urlparse(url)
    assert parsed.scheme == 'https'
    assert parsed.netloc == 'data.iledefrance-mobilites.fr'
    assert parsed.path == '/api/explore/v2.1/catalog/datasets/arrets-lignes/records'
    params = parse_qs(parsed.query)
    assert params['where'] == ['route_long_name="9"']
    assert params['limit'] == ['100']


def test_warning_message_matches_required_contract():
    stale = {'fetched_at': 1_700_000_000, 'expires_at': 1_700_000_060}
    msg = warning_for_stale_cache(stale, RuntimeError('boom'))
    assert msg.startswith('Warning: Transit data was last updated on ')
    assert 'Server error' in msg


def test_extracts_client_ip_from_forwarded_headers():
    headers = {'X-Forwarded-For': '203.0.113.7, 10.0.0.2'}
    assert get_client_ip_from_headers(headers) == '203.0.113.7'

    headers = {'Forwarded': 'for="[2001:db8::10]:443";proto=https'}
    assert get_client_ip_from_headers(headers) == '2001:db8::10'
