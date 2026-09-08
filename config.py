"""Single source of truth for target city, H3 resolutions, and scoring weights.

Swap CITY_NAME + BBOX to point the whole pipeline at a different city.
"""

CITY_NAME = "Austin, TX"

# Rough city-proper bounding box (not full metro) — keeps the res-6 ingestion
# sweep in the ~20-40 query range called for in the PRD's cost-control section.
BBOX = {
    "min_lat": 30.098,
    "max_lat": 30.517,
    "min_lng": -97.938,
    "max_lng": -97.568,
}

# Coarse grid used to tile ingestion sub-queries (one Nearby Search per centroid).
INGEST_H3_RESOLUTION = 6

# Finer grid used for TAM scoring / the dashboard choropleth.
SCORE_H3_RESOLUTION = 8

# Nearby Search radius (meters) per ingestion sub-query. Sized to roughly cover
# a resolution-6 hex (edge length ~3.23km) with some overlap so we don't miss
# restaurants near hex boundaries; overlap is de-duped downstream by place_id.
INGEST_RADIUS_METERS = 2000

# Restaurant place types to search for (Google Places `type` param).
PLACE_TYPES = ["restaurant"]

# --- TAM scoring weights (FR3) ---
# score = COUNT_WEIGHT * restaurant_count
#       + PRICE_WEIGHT * avg_price_level
#       with each independent-location restaurant's contribution multiplied by
#       CHAIN_DOWNWEIGHT_FACTOR if it matches a known chain (by name) or has
#       unusually high user_ratings_total (proxy for a multi-location brand).
COUNT_WEIGHT = 1.0
PRICE_WEIGHT = 2.0
CHAIN_DOWNWEIGHT_FACTOR = 0.3
CHAIN_RATINGS_THRESHOLD = 2000  # user_ratings_total above this is treated as a likely chain signal

# Small known-chain name list for v1 exclusion/down-weighting (case-insensitive substring match).
KNOWN_CHAINS = [
    "mcdonald's", "starbucks", "chipotle", "subway", "taco bell",
    "chick-fil-a", "panera bread", "wendy's", "burger king", "domino's",
    "pizza hut", "kfc", "whataburger", "panda express", "olive garden",
    "applebee's", "denny's", "ihop", "outback steakhouse", "jimmy john's",
]

# --- Territory clustering (FR4, stretch) ---
NUM_TERRITORIES = 5

RAW_RESTAURANTS_LOG = "data/raw_restaurants_log.jsonl"
RAW_RESTAURANTS_CSV = "data/raw_restaurants.csv"
RESTAURANTS_HEXED_CSV = "data/restaurants_hexed.csv"
HEX_SCORES_CSV = "data/hex_scores.csv"
TERRITORIES_CSV = "data/territories.csv"
DASHBOARD_HTML = "output/dashboard.html"
LOGO_PATH = "assets/sunday-logo.png"
