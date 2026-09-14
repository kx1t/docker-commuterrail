from __future__ import annotations

from typing import Any, Dict, Optional


def _coerce_float(value: Any) -> Optional[float]:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric:
        return None
    return numeric


def extract_mbta_vehicle_snapshot(payload: Dict[str, Any], transit: str, trip: str) -> Optional[Dict[str, Any]]:
    for vehicle in payload.get('data') or []:
        attributes = vehicle.get('attributes') or {}
        latitude = _coerce_float(attributes.get('latitude'))
        longitude = _coerce_float(attributes.get('longitude'))
        if latitude is None or longitude is None:
            continue
        return {
            'transit': transit,
            'trip': trip,
            'available': True,
            'latitude': latitude,
            'longitude': longitude,
            'bearing': _coerce_float(attributes.get('bearing')),
            'current_status': attributes.get('current_status'),
            'current_stop_sequence': attributes.get('current_stop_sequence'),
            'updated_at': attributes.get('updated_at'),
        }
    return None


def extract_paris_vehicle_snapshot(payload: Dict[str, Any], transit: str, trip: str) -> Optional[Dict[str, Any]]:
    deliveries = (((payload.get('Siri') or {}).get('ServiceDelivery') or {}).get('StopMonitoringDelivery') or [])
    for delivery in deliveries:
        for visit in delivery.get('MonitoredStopVisit') or []:
            journey = visit.get('MonitoredVehicleJourney') or {}
            location = journey.get('VehicleLocation') or {}
            latitude = _coerce_float(location.get('Latitude') or location.get('latitude'))
            longitude = _coerce_float(location.get('Longitude') or location.get('longitude'))
            if latitude is None or longitude is None:
                continue
            return {
                'transit': transit,
                'trip': trip,
                'available': True,
                'latitude': latitude,
                'longitude': longitude,
                'bearing': _coerce_float(location.get('Bearing') or location.get('bearing')),
                'current_status': journey.get('ProgressRate') or journey.get('JourneyNote'),
                'updated_at': (
                    journey.get('RecordedAtTime')
                    or journey.get('ValidUntilTime')
                    or journey.get('ExpectedArrivalTime')
                    or journey.get('ExpectedDepartureTime')
                ),
            }
    return None


def unavailable_snapshot(transit: str, trip: str, reason: str) -> Dict[str, Any]:
    return {
        'transit': transit,
        'trip': trip,
        'available': False,
        'reason': reason,
    }