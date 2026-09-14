from app.map_snapshot import extract_mbta_vehicle_snapshot, extract_paris_vehicle_snapshot, unavailable_snapshot


def test_extract_mbta_vehicle_snapshot_prefers_first_vehicle_with_coordinates():
    payload = {
        'data': [
            {'attributes': {'latitude': None, 'longitude': -71.0}},
            {'attributes': {'latitude': '42.1234', 'longitude': '-71.5678', 'bearing': '91', 'current_status': 'IN_TRANSIT_TO', 'current_stop_sequence': 8, 'updated_at': '2026-09-14T18:00:00Z'}},
        ]
    }

    snapshot = extract_mbta_vehicle_snapshot(payload, 'boston-bus', 'trip-123')

    assert snapshot is not None
    assert snapshot['transit'] == 'boston-bus'
    assert snapshot['trip'] == 'trip-123'
    assert snapshot['available'] is True
    assert snapshot['latitude'] == 42.1234
    assert snapshot['longitude'] == -71.5678
    assert snapshot['bearing'] == 91.0
    assert snapshot['current_status'] == 'IN_TRANSIT_TO'
    assert snapshot['current_stop_sequence'] == 8


def test_extract_paris_vehicle_snapshot_reads_vehicle_location_when_present():
    payload = {
        'Siri': {
            'ServiceDelivery': {
                'StopMonitoringDelivery': [
                    {
                        'MonitoredStopVisit': [
                            {
                                'MonitoredVehicleJourney': {
                                    'VehicleLocation': {
                                        'Latitude': '48.85',
                                        'Longitude': '2.35',
                                        'Bearing': '180',
                                    },
                                    'JourneyNote': 'Running',
                                }
                            }
                        ]
                    }
                ]
            }
        }
    }

    snapshot = extract_paris_vehicle_snapshot(payload, 'paris', 'journey-1')

    assert snapshot is not None
    assert snapshot['transit'] == 'paris'
    assert snapshot['trip'] == 'journey-1'
    assert snapshot['available'] is True
    assert snapshot['latitude'] == 48.85
    assert snapshot['longitude'] == 2.35
    assert snapshot['bearing'] == 180.0
    assert snapshot['current_status'] == 'Running'


def test_unavailable_snapshot_marks_position_missing():
    snapshot = unavailable_snapshot('paris', 'journey-1', 'No live vehicle coordinates are exposed.')

    assert snapshot == {
        'transit': 'paris',
        'trip': 'journey-1',
        'available': False,
        'reason': 'No live vehicle coordinates are exposed.',
    }