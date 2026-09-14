from app import server


def test_canonical_transit_name_maps_boston_aliases():
    assert server.canonical_transit_name('boston') == 'boston-commuterrail'
    assert server.canonical_transit_name('boston-commuterrair') == 'boston-commuterrail'
    assert server.canonical_transit_name('boston-subway') == 'boston-subway'
    assert server.canonical_transit_name('boston-bus') == 'boston-bus'


def test_upstream_url_for_new_boston_modes_reuses_mbta_api_base():
    for transit in ('boston-subway', 'boston-bus', 'boston-commuterrail'):
        url = server.upstream_url_for(transit, '/routes', {'filter[type]': '2'})
        assert url.startswith(server.MBTA_API)
        assert '/routes' in url
