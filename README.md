# GTM TAM & Territory Geo-Hex Mapper

A small, real, end-to-end GTM analytics pipeline built for a Sunday interview (GTM Data Analyst role, round 2). It uses no Sunday data — it pulls public Google Places data for Austin, TX, one of the six cities Sunday named as expansion targets in its Series B announcement — and treats restaurant density and price tier as a stand-in for the kind of TAM sizing and territory design work the role actually does.

The whole thing is built and run as an agentic, tool-calling pipeline in Claude Code, including a custom MCP server for the data ingestion step. It's a proof of concept, not a production system: single city, single run, self-contained HTML output, no auth or hosting.

## What was built

1. **Ingestion** — a custom MCP server (`mcp_server/`) exposes `search_restaurants`, wrapping Google Places Nearby Search with pagination and a running request counter. Claude Code called it once per hex centroid across a coarse tiling of Austin (41 queries at H3 resolution 6) to stay well under a sane API budget, rather than one call for the whole city. Every call durably appends its results to a log file, so the sweep survives across multiple tool calls in a session.
2. **Hex-gridding** — every restaurant is assigned to its containing hexagon at H3 resolution 8 (~0.46 km², Uber's H3 spatial index), the level used for scoring and the map.
3. **TAM scoring** — each hex gets a transparent, rules-based score (see below), not a black-box model.
4. **Territory clustering** *(stretch goal — completed)* — hexes are grouped into 5 balanced, roughly-equal-value sales territories.
5. **Dashboard** — a single self-contained HTML file (`output/dashboard.html`, Leaflet maps via folium) with a leadership view (citywide heatmap, KPIs, territory rollup) and an operator view (per-territory hex list and a ranked call list of the actual restaurants in a selected hex).

### This run, in numbers

- City: Austin, TX
- 932 raw records fetched → 878 `OPERATIONAL` restaurants across 196 scored hexes (54 records excluded as temporarily closed)
- 154 restaurants (~18%) flagged as likely chains and down-weighted
- Aggregate TAM score across all hexes: ~1,181
- 5 territories, balanced to within ~5% of each other by total score (231–242 points each)

## Scoring logic, in plain English

Two scores are computed, at two different levels of the funnel: one to size and heat-map the whole market (leadership), one to tell an individual operator who to call first inside a hex (operator).

**Hex-level TAM score** — "how much opportunity is in this hex?"

- Only currently-operating restaurants count.
- Each restaurant is worth 1.0 toward a hex's count, or 0.3 if it's flagged a likely chain — either its name matches a short known-chain list (McDonald's, Starbucks, Chipotle, etc.) or it has more than 2,000 Google reviews, a proxy for an established multi-location brand, since a single independent restaurant rarely accumulates that many. Sunday's ICP skews independent/operator-run, so chains still count, just less.
- Each hex also gets an average Google price level (1–4) across its restaurants, as a rough proxy for average check size.
- **score = (1.0 × chain-weighted restaurant count) + (2.0 × average price level).** Price level is weighted twice as heavily as raw count — a hex with fewer but pricier independent restaurants can outscore a hex that's merely dense with cheap chains.

**Restaurant-level priority score** — "inside this hex, who does an operator call first?"

A hex's restaurant *count* doesn't mean anything for one restaurant, so the per-restaurant score swaps in the two per-listing signals Google actually gives us — rating and review count — in place of that count term:

- **priority = chain-weight × price level × rating × log₁₀(review count + 1).**
- Review count is log-scaled so one runaway-popular restaurant with thousands of reviews doesn't automatically dominate every ranking by volume alone; a smaller, well-reviewed independent place still surfaces near the top.
- The same chain down-weight (0.3×) applies here too.
- A restaurant missing any one of price level, rating, or review count scores 0 and sorts to the bottom — that's a known, deliberate limitation, not a bug: roughly 40% of Google Places records have no reported price level.

## Territories and the two dashboard views

Territories are built by greedy nearest-neighbor growth — hexes are handed one at a time to whichever territory currently has the lowest total score, always picking the geographically closest unclaimed hex — rather than strict hex-to-hex adjacency. That's deliberate: this dataset's restaurant hotspots are naturally non-contiguous (196 scored hexes split into 59 disconnected islands under strict adjacency), so a distance-based greedy approach is what actually produces balanced, usable territories. Simple and explainable beat optimal here, on purpose.

The dashboard's leadership/operator toggle mirrors how the two audiences actually differ:

- **Leadership view** — a citywide choropleth colored by territory, a rollup table (hexes, restaurants, chains, avg price, total score per territory). Aggregate numbers for planning and reporting.
- **Operator view** — pick a territory, see every hex in it ranked by score, click a hex and its ranked call list of individual restaurants appears next to it (name and rank first, price/rating/review count as supporting detail). This is the artifact an operator would actually work from, not just a score to look at.

## How this maps to the GTM Data Analyst role

- **TAM sizing and territory design**: the scoring methodology here — define the addressable universe, build a transparent proxy score from observable signals, tier it into hexes and then territories — is structurally the same exercise as sizing a cross-sell base and building a propensity/fit signal, then tiering the result for a sales motion.
- **Leadership dashboards vs. operator-facing views**: the toggle between an aggregate rollup and a hex-level, restaurant-level call list is a direct match for the split the role itself draws between leadership reporting and the tools GTM operators actually work from day to day.
- **A concrete MCP example**: `mcp_server/` is a real, separately-runnable MCP server (`python -m mcp_server.server`), not a hypothetical — a specific, working answer to "where have you used AI tools or MCP."
- **Agentic workflows over static reports**: the entire pipeline — ingestion, hex assignment, scoring, clustering, rendering — was built and orchestrated as a multi-step, tool-calling build in Claude Code, which is the concrete version of "spot opportunities to apply agentic workflows" rather than a one-off script.

## Running it

There's no single entrypoint yet — that's the honest next iteration, not a hidden feature. The actual sequence, run once per city:

```bash
# 1. Ingestion happens by Claude Code calling the search_restaurants MCP tool
#    once per hex centroid from pipeline.ingest.get_ingestion_centroids(),
#    then materializing the durable per-call log into a CSV:
python -c "from pipeline.ingest import load_raw_log, save_raw_restaurants; save_raw_restaurants(load_raw_log())"

python -m pipeline.hex_assign    # data/raw_restaurants.csv       -> data/restaurants_hexed.csv
python -m pipeline.score         # data/restaurants_hexed.csv     -> data/hex_scores.csv
python -m pipeline.territories   # data/hex_scores.csv            -> data/territories.csv
python -m pipeline.render        # data/territories.csv           -> output/dashboard.html
```

Then open `output/dashboard.html` directly in a browser (self-contained, no server — just needs internet access at view time for the OpenStreetMap basemap tiles). See `CLAUDE.md` for environment setup and the `.mcp.json` server config.

## Known limitations

- Single city, single run — not a multi-region production system.
- No auth, no hosting, no test suite — a static HTML artifact is the whole "product."
- ~40% of Google Places records have no reported price level, which zeroes out that piece of both scores for those restaurants (documented above, not silently dropped).
- No single-command pipeline entrypoint yet, per "Running it" above.
