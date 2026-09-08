"""MCP server exposing `search_restaurants`, wrapping the Google Places API.

Run standalone for testing:
    python -m mcp_server.server

Registered with Claude Code via project MCP config so the pipeline calls it
as a real MCP tool rather than an inline API call.
"""

import json
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

import config
from mcp_server.places_client import get_request_count, search_nearby

load_dotenv()

mcp = MCPServer("places-server")


def _append_to_raw_log(records: list[dict]) -> None:
    """Durably persist fetched records as they come in, so a multi-call
    ingestion sweep survives without the caller having to transcribe results.
    """
    log_path = Path(config.RAW_RESTAURANTS_LOG)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


@mcp.tool()
def search_restaurants(lat: float, lng: float, radius_meters: int = 2000) -> list[dict]:
    """Search for restaurants near a lat/lng point via Google Places Nearby Search.

    Automatically follows pagination up to Google's ~60-result cap per query.
    Returns per-restaurant: place_id, name, lat, lng, price_level, rating,
    user_ratings_total, types, business_status. Each fetched record is also
    appended to the raw ingestion log (config.RAW_RESTAURANTS_LOG).
    """
    records = search_nearby(lat=lat, lng=lng, radius_meters=radius_meters, place_type="restaurant")
    _append_to_raw_log(records)
    return records


@mcp.tool()
def get_places_api_call_count() -> int:
    """Running count of billed Google Places requests made this server session.

    Includes pagination follow-ups, not just search_restaurants calls — use
    this to track spend before scaling a sweep up to the full city.
    """
    return get_request_count()


if __name__ == "__main__":
    mcp.run()
