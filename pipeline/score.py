"""FR3: score each hex on addressable opportunity (TAM proxy).

Input:  config.RESTAURANTS_HEXED_CSV (FR2 output)
Output: config.HEX_SCORES_CSV, one row per hex, sorted by score descending.

Scoring logic (see README for the plain-language version):
  - Only OPERATIONAL restaurants count toward TAM — permanently/temporarily
    closed locations aren't addressable right now.
  - A restaurant is flagged a likely chain if its name matches config.KNOWN_CHAINS
    (case-insensitive substring) or its user_ratings_total exceeds
    config.CHAIN_RATINGS_THRESHOLD (a single independent restaurant rarely
    accumulates that many reviews; it's a proxy for a well-known multi-location brand).
  - Each restaurant contributes 1.0 to a hex's weighted count, or
    config.CHAIN_DOWNWEIGHT_FACTOR if flagged a likely chain — Sunday's ICP
    skews independent/operator-run restaurants.
  - avg_price_level is the mean Google `price_level` (1-4) among restaurants
    that report one; hexes with no price data default to 0 (documented
    limitation — roughly 40% of records have no price_level).
  - score = COUNT_WEIGHT * weighted_count + PRICE_WEIGHT * avg_price_level
"""

import h3
import numpy as np
import pandas as pd

import config


def _is_chain(name: str, user_ratings_total: float) -> bool:
    name_lower = str(name).lower()
    if any(chain in name_lower for chain in config.KNOWN_CHAINS):
        return True
    return pd.notna(user_ratings_total) and user_ratings_total > config.CHAIN_RATINGS_THRESHOLD


def score_hexes(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["business_status"] == "OPERATIONAL"].copy()
    df["is_chain"] = [
        _is_chain(name, urt) for name, urt in zip(df["name"], df["user_ratings_total"])
    ]
    df["weight"] = df["is_chain"].map({True: config.CHAIN_DOWNWEIGHT_FACTOR, False: 1.0})

    hexes = df.groupby("hex_id").agg(
        restaurant_count=("place_id", "count"),
        chain_count=("is_chain", "sum"),
        weighted_count=("weight", "sum"),
        avg_price_level=("price_level", "mean"),
    ).reset_index()

    hexes["avg_price_level"] = hexes["avg_price_level"].fillna(0)
    hexes["lat"], hexes["lng"] = zip(*hexes["hex_id"].map(h3.cell_to_latlng))
    hexes["score"] = (
        config.COUNT_WEIGHT * hexes["weighted_count"]
        + config.PRICE_WEIGHT * hexes["avg_price_level"]
    )

    hexes = hexes.sort_values("score", ascending=False).reset_index(drop=True)
    return hexes[
        ["hex_id", "lat", "lng", "restaurant_count", "chain_count",
         "weighted_count", "avg_price_level", "score"]
    ]


def score_restaurants(df: pd.DataFrame) -> pd.DataFrame:
    """Per-restaurant analog of score_hexes, for the operator view's call list:
    inside one hex, which restaurant does an operator contact first?

    A hex's aggregate restaurant_count doesn't mean anything for a single
    restaurant, so this substitutes the individual signals Google gives us
    per listing — rating and user_ratings_total — as the weight on
    price_level, in place of the fixed PRICE_WEIGHT used at the hex level:

        priority_score = chain_weight * price_level * rating * log10(review_count + 1)

    review_count is log-scaled so a handful of runaway-popular chains don't
    mechanically dominate every ranking by review volume alone; price_level,
    rating, and review_count each default to 0 when Google has no value for
    that field (same fallback-to-0 policy as avg_price_level at the hex
    level), which means a restaurant missing any one of the three factors
    scores 0 and sorts to the bottom — a documented limitation, not a bug.
    """
    df = df[df["business_status"] == "OPERATIONAL"].copy()
    df["is_chain"] = [
        _is_chain(name, urt) for name, urt in zip(df["name"], df["user_ratings_total"])
    ]
    df["chain_weight"] = df["is_chain"].map({True: config.CHAIN_DOWNWEIGHT_FACTOR, False: 1.0})

    price_level = df["price_level"].fillna(0)
    rating = df["rating"].fillna(0)
    review_count = df["user_ratings_total"].fillna(0)

    df["priority_score"] = df["chain_weight"] * price_level * rating * np.log10(review_count + 1)

    return df.sort_values("priority_score", ascending=False).reset_index(drop=True)


def run(
    input_path: str = config.RESTAURANTS_HEXED_CSV,
    output_path: str = config.HEX_SCORES_CSV,
) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    hexes = score_hexes(df)
    hexes.to_csv(output_path, index=False)
    return hexes


if __name__ == "__main__":
    result = run()
    total_operational = result["restaurant_count"].sum()
    total_chain = result["chain_count"].sum()
    print(f"Scored {len(result)} hexes covering {total_operational} operational restaurants "
          f"({total_chain} flagged as likely chains).")
    print("\nTop 10 hexes by score:")
    print(result.head(10).to_string(index=False))
