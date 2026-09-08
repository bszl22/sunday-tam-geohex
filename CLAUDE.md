# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Environment**: Python 3.11+, dependencies in `.venv` (`pip install -r requirements.txt`). Requires a `.env` file with `GOOGLE_PLACES_API_KEY`. The MCP server config (`.mcp.json`) invokes `.venv/bin/python3` explicitly — bare `python3` resolves to a different interpreter (e.g. system/Anaconda) without the project's dependencies installed, which breaks the server silently.

**MCP server** (`places-server`, registered in `.mcp.json`): runs as a subprocess managed by Claude Code, exposing `search_restaurants(lat, lng, radius_meters)` and `get_places_api_call_count()`. To run/test it standalone outside Claude Code:
```
.venv/bin/python3 -m mcp_server.server
```
After editing `mcp_server/*.py`, the running MCP server process must be reconnected (`/mcp` in Claude Code) to pick up code changes — it does not hot-reload.

**Pipeline stages** — each is its own module under `pipeline/`, run in order, each reading/writing a file in `data/`:

1. **Ingestion** is not a standalone script — it happens by Claude Code calling the `search_restaurants` MCP tool once per hex centroid (from `pipeline.ingest.get_ingestion_centroids()`) during a session. Each call durably appends its results to `data/raw_restaurants_log.jsonl` server-side (see Architecture below), so a multi-call sweep survives without manual transcription. After a sweep, dedupe and materialize the raw CSV:
   ```python
   from pipeline.ingest import load_raw_log, save_raw_restaurants
   save_raw_restaurants(load_raw_log())  # writes data/raw_restaurants.csv
   ```
2. `python -m pipeline.hex_assign` — `data/raw_restaurants.csv` → `data/restaurants_hexed.csv` (adds H3 `hex_id` at `SCORE_H3_RESOLUTION`).
3. `python -m pipeline.score` — `data/restaurants_hexed.csv` → `data/hex_scores.csv` (TAM score per hex).
4. `python -m pipeline.territories` — `data/hex_scores.csv` → `data/territories.csv` (adds `territory_id`).
5. `python -m pipeline.render` — `data/territories.csv` (falls back to `data/hex_scores.csv` if territories weren't built) → `output/dashboard.html`.

There is no single `run_pipeline.py` entrypoint yet — stages 2-5 are run manually in order after an ingestion sweep.

**Viewing the dashboard**: open `output/dashboard.html` directly in a browser (self-contained single file, no server required). It does need internet access at view time, since the Leaflet/OpenStreetMap basemap tiles load from a CDN.

No test suite, linter, or formatter is currently configured in this repo.

## Architecture

- **`README.md`** documents both scoring formulas (hex TAM score and per-restaurant priority score) in plain language, the pipeline, and how the project maps to the interview role — it's the FR6 deliverable referenced throughout this doc and in `score.py`'s docstrings. Keep it in sync when a scoring formula or weight changes.

- **`config.py` is the single source of truth**: target city (name + bounding box), H3 resolutions (`INGEST_H3_RESOLUTION=6` for the coarse ingestion tiling, `SCORE_H3_RESOLUTION=8` for scoring/rendering), TAM scoring weights, the known-chains list, territory count, every data/output file path, and `LOGO_PATH` (the brand logo embedded in the dashboard header). Swap the target city or tune scoring here, not in the pipeline modules.

- **Pipeline shape**: ingest (MCP-orchestrated) → `hex_assign` → `score` → `territories` → `render`. Each stage is a separate module with a plain CSV in/out, so any stage can be re-run or debugged independently of the others.

- **`mcp_server/`**: `places_client.py` wraps Google Places Nearby Search (pagination past the ~60-result cap, field selection, a module-level running request counter). `server.py` exposes it via `mcp.server.mcpserver.MCPServer` (note: this is the `mcp` v2.x API — `MCPServer`, not the v1 `mcp.server.fastmcp.FastMCP`). The `search_restaurants` tool has a side effect beyond returning data: it appends every fetched record to `data/raw_restaurants_log.jsonl` as it goes, which is what makes a multi-call ingestion sweep durable.

- **TAM scoring** (`pipeline/score.py`): only `OPERATIONAL` restaurants count. A restaurant is flagged a likely chain by matching `config.KNOWN_CHAINS` (case-insensitive substring) or by `user_ratings_total` exceeding `config.CHAIN_RATINGS_THRESHOLD`; chain-flagged restaurants contribute `config.CHAIN_DOWNWEIGHT_FACTOR` (default 0.3) instead of 1.0 to a hex's weighted restaurant count. `score = COUNT_WEIGHT * weighted_count + PRICE_WEIGHT * avg_price_level`, where `avg_price_level` excludes restaurants with no reported price level and defaults to 0 for a hex with no price data at all.

- **Restaurant-level priority scoring** (`pipeline/score.py::score_restaurants`): a second, per-restaurant scoring function that powers the operator view's call list (see below) — a hex's aggregate score doesn't tell an operator which specific restaurant to contact first. Reuses the same chain down-weight as the hex score but substitutes each listing's own `rating`/`user_ratings_total` for the hex-level count term: `priority_score = chain_weight * price_level * rating * log10(review_count + 1)`. Review count is log-scaled so one high-volume listing can't mechanically dominate every hex. `price_level`, `rating`, and `review_count` each default to 0 when Google has no value, so a restaurant missing any one of the three scores 0 and sorts last — same fallback-to-0 policy as the hex score, not a bug. Called from `render.py::_load_scored_restaurants`, which reads `config.RAW_RESTAURANTS_CSV` directly (not the hex/territory rollups) and returns `None` if that file doesn't exist yet, so the operator view degrades gracefully rather than erroring.

- **Territory clustering** (`pipeline/territories.py`): deliberately does not use strict H3 hex adjacency as the grouping rule. Restaurant hotspots at resolution 8 are naturally non-contiguous (verified on this dataset: 196 scored hexes split into 59 disconnected islands under strict shared-edge adjacency, the largest just 12 hexes). Territories instead grow by geographic nearest-neighbor distance, always handing the next-closest unclaimed hex to whichever territory currently has the lowest total score — this is what keeps the resulting territories balanced.

- **Dashboard rendering** (`pipeline/render.py`): folium maps are embedded via a custom `_map_to_iframe()` helper (plain `srcdoc` iframe of `m.get_root().render()`), not folium's own `Map._repr_html_()` — that method is Jupyter-oriented and falls back to a visible "Trust Notebook" message when its iframe fails to render. The leadership/operator territory toggle keeps all 6 possible maps (1 citywide/leadership view + 5 per-territory views) simultaneously mounted in one fixed-height CSS grid stack and switches between them with `visibility` rather than `hidden`/`display:none` — a Leaflet map that initializes inside a `display:none` ancestor is permanently mis-sized even after later being shown, so no element in the toggle hierarchy may use `display:none` on anything containing a map.

- **Table row → map focus** (`pipeline/render.py`): clicking a hex row in the top-10 table or an operator-view hex table pans/zooms the relevant map to that hex and flashes a pulsing outline around it. This works across the parent-page/iframe boundary: `_add_highlight_support()` injects a `window.dashboardHighlight(lat, lng, boundary)` function into *every* map's own srcdoc document (it's mounted there, not in the parent, since each map is its own Leaflet instance); the parent page's `focusMapHex()` (in `TOGGLE_JS`) reaches into the target `<iframe>`'s `contentWindow` and calls it directly. This cross-document call is allowed because `srcdoc` iframes inherit the parent document's origin. Each table row's `onclick` carries the hex's centroid and its full H3 boundary as a plain JS array literal (`_hex_boundary_js()` — no quoting needed since it's just numbers), targeting either `#main-map-frame` (the citywide map) or `.map-layer[data-key=territory-N] iframe` (a specific territory layer inside the map-stack).

- **Operator view call list** (`pipeline/render.py`): the operator view's real differentiator from leadership isn't the hex table (leadership has one too) — it's that clicking a hex row also reveals the ranked list of individual restaurants inside it (`_call_list_panel_html()`/`_call_list_items_html()`, scored by `score_restaurants()` above), the actual list an operator would work from. Every hex's call-list is pre-rendered as a hidden `<div data-hex="...">` and `showCallList(hexId)` just toggles which one is visible — same mounted-but-hidden pattern as the map-stack, and it means a click never needs to compute or fetch anything client-side. The hex table (left) and call list (right) are laid out as a fixed-height, independently-scrollable two-pane grid (`.operator-split`) rather than stacked — with ~40 hexes per territory, stacking would have buried the call list below a full page of scrolling.

- **Dashboard theme is Sunday's brand palette**, not a generic dark mode: near-black page (`#000000`)/card (`#181818`) surfaces, white/light-gray text, and a `#FF17E9` magenta accent (header rule, KPI top-stripe, top-10 #1 row). The hex heatmap uses `SEQUENTIAL_MAGENTA`, a 2-stop linear ramp (`#FFF3FE` → `#FF17E9`) — deliberately just 2 stops so full-saturation magenta is reserved for the actual max-score hex by construction, not hand-tuned. This is a separate color system from `TERRITORY_COLORS` below; don't conflate or re-derive one from the other.
  - **The map basemap must stay OpenStreetMap tiles + a CSS filter, not a "real" dark tile provider.** Both CartoDB `dark_matter` and Esri's Dark Gray Canvas were tried and both break under this project's `srcdoc`-iframe embedding specifically: CartoDB watermarks anonymous tile requests with "API KEY REQUIRED", and Esri's service silently returns blank/gray tiles when the request has no Referer/Origin header (true of any `srcdoc` iframe's opaque origin) — confirmed by diffing a standalone render (real tiles) against the embedded one (blank, same HTTP 200s). `MAP_TILES = "OpenStreetMap"` plus `DARK_TILE_FILTER_CSS` (a `grayscale/invert/brightness/contrast` filter scoped to `.leaflet-tile-pane` only, via `_add_dark_basemap()`) is the fix — don't swap in a dark tile URL without re-testing inside the actual embedded iframe, not a standalone map.
  - The logo at `config.LOGO_PATH` (`assets/sunday-logo.png`) is a modified version of Sunday's mark: background stripped to transparency and the wordmark recolored white (the original had a black wordmark on white, illegible on this dark theme); the magenta squiggle mark is untouched. It's embedded into the dashboard as a base64 data URI (not an `<img src="assets/...">` reference) so `output/dashboard.html` stays a single self-contained file per the architecture's "no server required" design.

- Territory identity colors (`TERRITORY_COLORS`) use the dataviz skill's default categorical palette, of which only the first 3 slots are validated for simultaneous ("all-pairs") display — with 5 territories shown at once on one choropleth, every territory also gets a direct numeral label on the map (not color alone) as a required secondary identification channel.

---

# PRD: GTM TAM & Territory Geo-Hex Mapper (Sunday Interview Project)

## 1. Background & Purpose

This is a personal portfolio project built to demonstrate GTM analytics capability for a Sunday interview (GTM Data Analyst role, round 2 with Alexander). It is not a Sunday-owned project and uses no Sunday internal data — it uses public Google Places data as a stand-in for the kind of TAM sizing and territory design work the role actually does.

The role's core mandate: be the primary analytics resource for GTM operators across regions, build leadership dashboards and KPI reporting across the sales funnel, and proactively find opportunities to apply AI/agentic workflows rather than just maintain static reports. This project is a small, real, end-to-end demonstration of exactly that: an agentic pipeline (built and run with Claude Code) that pulls restaurant data for one of Sunday's own publicly announced expansion cities, hex-grids the market, scores it for TAM, and renders a dashboard suitable for a leadership review.

## 2. Goals

**MVP (must ship by Wednesday):**
- Pull restaurant-level data for one target city via the Google Places API.
- Aggregate that data into a hexagonal grid (H3) covering the city.
- Score each hex on addressable opportunity (TAM proxy).
- Render a single dashboard: a hex heatmap by TAM score + a KPI summary panel (total addressable restaurants, estimated aggregate opportunity, top 10 hexes).

**Stretch (only if MVP lands early):**
- Cluster hexes into balanced sales territories (contiguous groups of roughly equal TAM).
- Add a second dashboard view toggling between a leadership rollup (TAM by territory) and an operator view (hex-level detail within one territory).

## 3. Non-Goals

- No access to and no use of real Sunday data — Google Places is the only data source.
- Not a production multi-region system — single city, single run, proof of concept.
- Not a polished web app with auth/hosting — a self-contained HTML/static output is sufficient.
- Not attempting a statistically rigorous propensity model — a transparent, defensible rules-based score is preferred over a black-box one, since the interview story needs to be explainable end to end.

## 4. Target Market

Default: **Austin, TX** — one of the six cities Sunday named as expansion targets in its Series B announcement (alongside LA, Dallas, DC, Philadelphia, Miami). Mid-sized, restaurant-dense, well-covered by Google Places. Should be a config value (city name + bounding box), not hardcoded, so it can be swapped in five minutes if a different city tells a better story.

## 5. Functional Requirements

**FR1 — Data ingestion via MCP.**
Build a small custom MCP server that exposes a tool (e.g. `search_restaurants`) wrapping the Google Places API. Claude Code should call this as an MCP tool during the build/run, not as an inline API call buried in a script — the point of the exercise is a real, reusable MCP integration, which is a direct talking point for the interview's AI/agentic-workflow and MCP topics.

The tool should:
- Accept a lat/lng + radius (or bounding box) and return restaurants in that area.
- Paginate through Google's result limits automatically.
- Return, per result: `place_id`, `name`, `lat`, `lng`, `price_level`, `rating`, `user_ratings_total`, `types`, `business_status`.
- Since Nearby Search caps at ~60 results per query, the city must be covered by tiling multiple sub-queries (e.g., one query per H3 hex centroid at a coarse resolution) rather than one call for the whole city.

**FR2 — Geo-hex aggregation.**
Use H3 (Uber's hexagonal hierarchical spatial index, `h3-py`) to hex-grid the target city. Resolution 8 (~0.46 km² per hex) is a reasonable default for a city-scale view — dense enough to show meaningful variation, coarse enough to stay readable. Assign every restaurant returned by FR1 to its containing hex.

**FR3 — TAM scoring.**
Per hex, compute a transparent TAM score from: restaurant count, average `price_level` (proxy for average check size), and optionally a filter/down-weight for likely chains (e.g. by name matching against a small known-chain list, or by unusually high `user_ratings_total` relative to a single location, since Sunday's ICP skews independent/operator-run restaurants). Document the exact formula and weights in the README — being able to explain the scoring logic plainly is more valuable for the interview than sophistication.

**FR4 — Territory clustering (stretch).**
Greedily group adjacent hexes into N contiguous territories with roughly balanced total TAM score. Simplicity over optimality — a clear, explainable greedy approach beats an unexplainable optimizer.

**FR5 — Dashboard.**
A single self-contained HTML output (folium or plotly for the map layer) showing:
- A choropleth of hexes colored by TAM score.
- A KPI summary strip: total addressable restaurants, estimated aggregate opportunity, count of hexes scored.
- A ranked list of the top 10 hexes by score, with the driving factors visible (count, avg price level).
- (Stretch) A toggle or second panel showing territory boundaries and per-territory rollups.

**FR6 — Output artifacts for the email.**
A static screenshot (PNG) or short screen recording of the dashboard, plus a one-paragraph README explaining what was built, the scoring logic, and how it maps to the GTM Data Analyst role's core outputs (TAM/territory work, leadership dashboards, agentic workflow, MCP usage).

## 6. Technical Approach

- **Language/stack:** Python 3.11+.
- **Key libraries:** `h3` (geo-hexing), `googlemaps` or plain `requests` (Places API calls), `pandas` (aggregation), `folium` or `plotly` (map rendering).
- **MCP server:** a minimal Python MCP server (using the official `mcp` SDK) exposing the `search_restaurants` tool described in FR1. Runs locally, registered in Claude Code's MCP config for this project.
- **Pipeline shape:** ingest → hex-assign → score → (cluster) → render. Each stage should be its own script/module with a clear input/output (e.g. a CSV or parquet file between stages), so Claude Code can build, test, and debug one stage at a time rather than one monolithic script.
- **API cost control:** Places API billing is per-request. Cap the number of sub-queries (e.g., start with a coarse resolution-6 tiling of the city for the ingestion sweep, ~20-40 queries, well under typical free-tier/budget limits) and log a running request count. Test on a small radius (a few hexes) before running the full city sweep.

## 7. Data Requirements

From the Google Places API (Nearby Search or Text Search — Nearby Search is the better fit here):
`place_id`, `name`, `geometry.location` (lat/lng), `price_level`, `rating`, `user_ratings_total`, `types`, `business_status`.

Requires: a Google Cloud project, the Places API enabled, billing enabled on the project, and an API key (restricted to the Places API and, ideally, to this project's IP/usage to avoid surprise charges).

## 8. Acceptance Criteria

- MVP dashboard renders successfully for the full target city from a single documented run (a `make run` / `python run_pipeline.py` or equivalent — one command, not five manual steps).
- TAM scoring logic is written in plain language in the README, not just in code.
- The MCP server is a real, separately runnable/testable component, not inlined logic.
- Output includes at least one exportable static image suitable for pasting into an email.
- If the stretch goal is skipped, the README should say so explicitly and describe it as "the obvious next iteration" — that's a fine, honest thing to say in an interview follow-up too.

## 9. Interview Narrative Hooks (for Bryan, not for Claude Code)

Map the finished pieces back to talking points directly:
- The scoring methodology (define universe → score by proxy signals → validate against known-dense areas) mirrors the WP Ecom cross-sell TAM story's structure (define addressable base → build a propensity/fit signal → tier it).
- The leadership-rollup vs. hex-level-detail split (if the stretch goal is built) mirrors the JD's own split between leadership dashboards and operator-facing views.
- The MCP server is a concrete, specific answer to "where have you used AI tools / MCPs" — not a hypothetical.
- The whole pipeline being orchestrated through Claude Code as a multi-step tool-calling build is the concrete answer to the JD's "spot opportunities to deploy agentic workflows" mandate.
