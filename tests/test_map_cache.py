import time

from app import server


def test_get_or_refresh_cached_payload_reuses_shared_cache_refresh_path():
    server.cache_store.clear()
    server.refresh_inflight.clear()
    server.transit_last_requested.clear()

    called = []
    original_fetch_and_cache = server.fetch_and_cache

    def fake_fetch_and_cache(transit, endpoint, query, client_ip=None, user_agent=None):
        called.append((transit, endpoint, query, client_ip, user_agent))
        return {
            'data': {'data': [{'attributes': {'latitude': '42.1', 'longitude': '-71.2'}}]},
            'expires_at': server.current_timestamp() + 60,
            'fetched_at': server.current_timestamp(),
        }

    server.fetch_and_cache = fake_fetch_and_cache
    try:
        server.mark_transit_requested('boston-commuterrail')
        cached = server.get_or_refresh_cached_payload(
            'boston-commuterrail',
            '/vehicles',
            {'filter[trip]': 'trip-1', 'page[limit]': '10'},
        )
    finally:
        server.fetch_and_cache = original_fetch_and_cache

    assert called == [
        (
            'boston-commuterrail',
            '/vehicles',
            {'filter[trip]': 'trip-1', 'page[limit]': '10'},
            None,
            None,
        )
    ]
    assert cached is not None
    assert cached['data']['data'][0]['attributes']['latitude'] == '42.1'


def test_build_vehicle_snapshot_marks_stale_cached_position_as_warning():
    cached_vehicle_entry = {
        'data': {
            'data': [
                {
                    'attributes': {
                        'latitude': '42.3601',
                        'longitude': '-71.0589',
                        'updated_at': '2026-09-14T18:00:00Z',
                    }
                }
            ]
        },
        'expires_at': time.time() - 5,
        'fetched_at': time.time() - 15,
    }

    payload = server.build_vehicle_snapshot(
        'boston-commuterrail',
        {'trip': 'trip-2'},
        cached_vehicle_entry,
    )

    assert payload['status'] == 'warning'
    assert payload['data']['available'] is True
    assert payload['data']['latitude'] == 42.3601
    assert 'Data is stale.' in payload['message']


def test_build_vehicle_snapshot_accepts_single_vehicle_object_payload():
    cached_vehicle_entry = {
        'data': {
            'data': {
                'id': '1718',
                'attributes': {
                    'latitude': '42.680553',
                    'longitude': '-71.149925',
                    'bearing': '149',
                },
            }
        },
        'expires_at': time.time() + 60,
        'fetched_at': time.time() - 2,
    }

    payload = server.build_vehicle_snapshot(
        'boston-commuterrail',
        {'trip': 'NorthBase-825696-244', 'vehicle': '1718'},
        cached_vehicle_entry,
    )

    assert payload['status'] == 'ok'
    assert payload['data']['available'] is True
    assert payload['data']['trip'] == 'NorthBase-825696-244'
    assert payload['data']['latitude'] == 42.680553
    assert payload['data']['longitude'] == -71.149925


def test_build_vehicle_snapshot_allows_vehicle_without_trip():
    cached_vehicle_entry = {
        'data': {
            'data': {
                'id': '1800',
                'attributes': {
                    'latitude': '42.41119',
                    'longitude': '-71.07676',
                },
            }
        },
        'expires_at': time.time() + 60,
        'fetched_at': time.time(),
    }

    payload = server.build_vehicle_snapshot(
        'boston-commuterrail',
        {'vehicle': '1800'},
        cached_vehicle_entry,
    )

    assert payload['status'] == 'ok'
    assert payload['data']['available'] is True
    assert payload['data']['trip'] == '1800'


def test_extract_mbta_route_from_trip_payload_reads_relationship_route_id():
    payload = {
        'data': {
            'id': 'NorthBase-825641-1240',
            'type': 'trip',
            'relationships': {
                'route': {
                    'data': {'id': 'CR-Haverhill', 'type': 'route'}
                }
            },
        }
    }

    route = server.extract_mbta_route_from_trip_payload(payload)

    assert route == 'CR-Haverhill'


def test_schedule_cache_key_buckets_rolling_time_window():
    first = server.read_cache_key(
        'boston-bus',
        '/schedules',
        {'min_time': '2026-09-15T12:01:00+00:00', 'max_time': '2026-09-16T06:01:00+00:00', 'filter[stop]': '2332'},
    )
    second = server.read_cache_key(
        'boston-bus',
        '/schedules',
        {'min_time': '2026-09-15T12:29:00+00:00', 'max_time': '2026-09-16T06:29:00+00:00', 'filter[stop]': '2332'},
    )

    assert first == second


def test_cache_ttl_uses_base_authority_endpoint_defaults():
    assert server.cache_ttl_seconds('boston-bus', '/schedules') == server.SCHEDULE_CACHE_TTL_SECONDS
    assert server.cache_ttl_seconds('boston-subway', '/predictions') == server.PREDICTION_CACHE_TTL_SECONDS
    assert server.cache_ttl_seconds('boston-commuterrail', '/vehicles') == server.VEHICLE_CACHE_TTL_SECONDS