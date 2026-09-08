"""Thin wrapper around Google Places Nearby Search: pagination + field selection."""

import os
import time

import googlemaps

FIELDS = (
    "place_id", "name", "lat", "lng", "price_level", "rating",
    "user_ratings_total", "types", "business_status",
)

# Google returns a next_page_token that isn't valid for a few seconds.
PAGE_TOKEN_DELAY_SECONDS = 2

# Running count of billed Nearby Search requests made this server session
# (cost-control logging per the PRD — includes pagination follow-ups).
_request_count = 0


def get_request_count() -> int:
    return _request_count


def _client() -> googlemaps.Client:
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY is not set (check .env)")
    return googlemaps.Client(key=api_key)


def _to_record(result: dict) -> dict:
    location = result.get("geometry", {}).get("location", {})
    return {
        "place_id": result.get("place_id"),
        "name": result.get("name"),
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "price_level": result.get("price_level"),
        "rating": result.get("rating"),
        "user_ratings_total": result.get("user_ratings_total"),
        "types": result.get("types", []),
        "business_status": result.get("business_status"),
    }


def search_nearby(lat: float, lng: float, radius_meters: int, place_type: str = "restaurant") -> list[dict]:
    """Nearby Search around (lat, lng), following pagination up to Google's 60-result cap.

    Returns a list of records with just the fields FR1 asks for.
    """
    global _request_count
    client = _client()
    records: list[dict] = []

    response = client.places_nearby(location=(lat, lng), radius=radius_meters, type=place_type)
    _request_count += 1
    records.extend(_to_record(r) for r in response.get("results", []))

    next_token = response.get("next_page_token")
    while next_token:
        time.sleep(PAGE_TOKEN_DELAY_SECONDS)
        response = client.places_nearby(page_token=next_token)
        _request_count += 1
        records.extend(_to_record(r) for r in response.get("results", []))
        next_token = response.get("next_page_token")

    return records
