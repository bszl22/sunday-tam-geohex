"""FR4 (stretch): greedily group hexes into N balanced, geographically compact territories.

Input:  config.HEX_SCORES_CSV (FR3 output)
Output: config.TERRITORIES_CSV (hex_scores rows + a `territory_id` column)

A note on "adjacent": restaurant hotspots at resolution-8 (~0.46 km² per hex)
are naturally not edge-to-edge contiguous — there are always gaps between them
(parks, residential blocks, water, low-density stretches). Checked directly on
this dataset: the 196 scored hexes split into 59 disconnected islands under
strict shared-edge adjacency, the largest just 12 hexes. Territory-building on
literal hex adjacency would mostly be building single-hex "territories."

So "adjacent" here means geographically nearest, not edge-sharing — the same
notion a human territory planner uses when circling hotspots on a map and
grouping whichever cluster is closest. The algorithm stays simple and
explainable:

  1. Pick config.NUM_TERRITORIES seed hexes: highest score first, skipping any
     hex too close to a seed already chosen, so the starting points are spread
     across the city rather than clumped together.
  2. Grow every territory at once, one hex per step: find whichever territory
     currently has the lowest total score, and hand it whichever unclaimed hex
     is geographically closest to it. Repeat until every hex is claimed. The
     territory that's behind always gets the next pick, which is what keeps
     the five totals from drifting apart.
  Finally, territories are relabeled 1..N by total score descending, so
  "Territory 1" is always the highest-opportunity territory.
"""

import pandas as pd

import config


def _dist2(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    return (lat_a - lat_b) ** 2 + (lng_a - lng_b) ** 2


def build_territories(hexes: pd.DataFrame, num_territories: int = config.NUM_TERRITORIES) -> pd.DataFrame:
    hexes = hexes.sort_values("score", ascending=False).reset_index(drop=True)
    hex_ids = list(hexes["hex_id"])
    score_by_hex = dict(zip(hexes["hex_id"], hexes["score"]))
    lat_by_hex = dict(zip(hexes["hex_id"], hexes["lat"]))
    lng_by_hex = dict(zip(hexes["hex_id"], hexes["lng"]))

    # Step 1 — spread-out seeds: skip candidates too close to a seed already picked.
    lat_span = hexes["lat"].max() - hexes["lat"].min()
    lng_span = hexes["lng"].max() - hexes["lng"].min()
    min_seed_sep2 = ((lat_span ** 2 + lng_span ** 2) ** 0.5 / (num_territories * 1.5)) ** 2

    seeds: list[str] = []
    for h in hex_ids:
        if len(seeds) == num_territories:
            break
        if all(
            _dist2(lat_by_hex[h], lng_by_hex[h], lat_by_hex[s], lng_by_hex[s]) >= min_seed_sep2
            for s in seeds
        ):
            seeds.append(h)
    for h in hex_ids:
        if len(seeds) == num_territories:
            break
        if h not in seeds:
            seeds.append(h)

    territory_hexes = {t: [seed] for t, seed in enumerate(seeds)}
    territory_score = {t: score_by_hex[seed] for t, seed in enumerate(seeds)}
    assigned = {seed: t for t, seed in enumerate(seeds)}
    remaining = [h for h in hex_ids if h not in assigned]

    # Step 2 — grow the currently-smallest territory into its nearest unclaimed hex.
    while remaining:
        t = min(territory_score, key=territory_score.get)
        nearest = min(
            remaining,
            key=lambda h: min(
                _dist2(lat_by_hex[h], lng_by_hex[h], lat_by_hex[m], lng_by_hex[m])
                for m in territory_hexes[t]
            ),
        )
        territory_hexes[t].append(nearest)
        territory_score[t] += score_by_hex[nearest]
        assigned[nearest] = t
        remaining.remove(nearest)

    # Relabel 1..N by total score descending, so "Territory 1" = highest opportunity.
    rank_order = sorted(territory_score, key=territory_score.get, reverse=True)
    relabel = {old: new + 1 for new, old in enumerate(rank_order)}
    hexes["territory_id"] = hexes["hex_id"].map(lambda h: relabel[assigned[h]])
    return hexes


def territory_rollup(hexes_with_territory: pd.DataFrame) -> pd.DataFrame:
    rollup = hexes_with_territory.groupby("territory_id").agg(
        hex_count=("hex_id", "count"),
        restaurant_count=("restaurant_count", "sum"),
        chain_count=("chain_count", "sum"),
        avg_price_level=("avg_price_level", "mean"),
        total_score=("score", "sum"),
    ).reset_index()
    return rollup.sort_values("total_score", ascending=False).reset_index(drop=True)


def run(input_path: str = config.HEX_SCORES_CSV, output_path: str = config.TERRITORIES_CSV) -> pd.DataFrame:
    hexes = pd.read_csv(input_path)
    hexes = build_territories(hexes)
    hexes.to_csv(output_path, index=False)
    return hexes


if __name__ == "__main__":
    result = run()
    rollup = territory_rollup(result)
    print(f"Built {result['territory_id'].nunique()} territories from {len(result)} hexes.")
    print(rollup.to_string(index=False))
