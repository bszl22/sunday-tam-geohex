"""FR2: assign every restaurant to its containing hex at config.SCORE_H3_RESOLUTION.

Input:  config.RAW_RESTAURANTS_CSV (FR1 output)
Output: config.RESTAURANTS_HEXED_CSV (adds a `hex_id` column)
"""

import h3
import pandas as pd

import config


def assign_hexes(df: pd.DataFrame, resolution: int = config.SCORE_H3_RESOLUTION) -> pd.DataFrame:
    """Add a `hex_id` column: the resolution-`resolution` H3 cell containing each row's (lat, lng)."""
    df = df.copy()
    df["hex_id"] = [h3.latlng_to_cell(lat, lng, resolution) for lat, lng in zip(df["lat"], df["lng"])]
    return df


def run(
    input_path: str = config.RAW_RESTAURANTS_CSV,
    output_path: str = config.RESTAURANTS_HEXED_CSV,
) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    df = assign_hexes(df)
    df.to_csv(output_path, index=False)
    return df


if __name__ == "__main__":
    result = run()
    print(f"Assigned {len(result)} restaurants to {result['hex_id'].nunique()} hexes at resolution {config.SCORE_H3_RESOLUTION}.")
