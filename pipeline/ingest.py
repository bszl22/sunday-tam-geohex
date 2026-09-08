"""FR1/FR2 ingestion stage: tile the target city into a coarse H3 grid.

Google Places calls happen only through the `search_restaurants` MCP tool
(mcp_server/), invoked by Claude Code while orchestrating a run — this module
just computes what to query (hex centroids) and assembles what comes back
(dedupe + write the raw restaurants CSV). It has no Places API dependency.
"""

import json

import pandas as pd
import h3

import config


def get_ingestion_centroids() -> list[dict]:
    """Coarse-resolution hex centroids tiling the city bounding box.

    Each centroid is the origin of one Nearby Search sub-query (FR1);
    config.INGEST_RADIUS_METERS is sized to cover one hex at
    config.INGEST_H3_RESOLUTION with some overlap.
    """
    bbox = config.BBOX
    poly = h3.LatLngPoly([
        (bbox["min_lat"], bbox["min_lng"]),
        (bbox["min_lat"], bbox["max_lng"]),
        (bbox["max_lat"], bbox["max_lng"]),
        (bbox["max_lat"], bbox["min_lng"]),
    ])
    cells = h3.polygon_to_cells(poly, config.INGEST_H3_RESOLUTION)
    return [
        {"hex_id": cell, "lat": lat, "lng": lng}
        for cell, (lat, lng) in sorted(
            (cell, h3.cell_to_latlng(cell)) for cell in cells
        )
    ]


def save_raw_restaurants(records: list[dict], path: str = config.RAW_RESTAURANTS_CSV) -> pd.DataFrame:
    """Dedupe fetched restaurant records by place_id and write the raw CSV.

    Adjacent ingestion tiles overlap on purpose (FR1) so restaurants near a
    hex boundary aren't missed by either query; dedupe here reconciles that.
    """
    df = pd.DataFrame.from_records(records)
    df = df.drop_duplicates(subset="place_id").reset_index(drop=True)
    df.to_csv(path, index=False)
    return df


def load_raw_log(log_path: str = config.RAW_RESTAURANTS_LOG) -> list[dict]:
    """Read the append-only JSONL log the search_restaurants MCP tool writes
    to during an ingestion sweep (one line per fetched record)."""
    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
