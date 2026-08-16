"""Step 2: roll up observations by HUC12 watershed and rank by severity.

NO3 >= 10 ppm matches the "Poor" bin start in the source file's own
Scorecard sheet. Orthophosphate uses >= 100 ppb (the first bin above 0,
i.e. any detectable phosphate) rather than the Scorecard's 500 ppb "Poor"
cutoff -- 500 ppb reproduced near-zero "% High Phos" rates, while 100 ppb
exactly reproduces the Section 5 table's percentages (verified against
Little Volga River/Coulee Creek/Headwaters Volga/Brush Creek).
"""
import pandas as pd

from load_tudb import load_tudb

MIN_OBSERVATIONS = 5
NO3_IMPAIRED_PPM = 10
PHOS_IMPAIRED_PPB = 100

DISTURBANCE_COLS = [
    "bank_erosion", "algal_bloom", "livestock_in_water", "pipe_drain_outflow",
    "trash", "fish_kill", "fish_barrier",
]


def rollup_by_huc12(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["no3_impaired"] = df["no3_ppm"] >= NO3_IMPAIRED_PPM
    df["phos_impaired"] = df["orthophosphate_ppb"] >= PHOS_IMPAIRED_PPB

    grouped = df.groupby(["huc12_code", "huc12_name", "huc10_name"])

    rollup = grouped.agg(
        n_observations=("huc12_code", "size"),
        n_no3_readings=("no3_ppm", "count"),
        avg_no3_ppm=("no3_ppm", "mean"),
        pct_no3_impaired=("no3_impaired", "mean"),
        n_phos_readings=("orthophosphate_ppb", "count"),
        avg_orthophosphate_ppb=("orthophosphate_ppb", "mean"),
        pct_phos_impaired=("phos_impaired", "mean"),
    ).reset_index()

    for col in DISTURBANCE_COLS:
        flags = grouped[col].apply(lambda s: (s == 1).sum())
        rollup = rollup.merge(
            flags.rename(f"{col}_flags"),
            left_on=["huc12_code", "huc12_name", "huc10_name"],
            right_index=True,
        )

    rollup["pct_no3_impaired"] = (rollup["pct_no3_impaired"] * 100).round(0)
    rollup["pct_phos_impaired"] = (rollup["pct_phos_impaired"] * 100).round(0)
    rollup["avg_no3_ppm"] = rollup["avg_no3_ppm"].round(1)
    rollup["avg_orthophosphate_ppb"] = rollup["avg_orthophosphate_ppb"].round(0)

    reliable = rollup[rollup["n_observations"] >= MIN_OBSERVATIONS].copy()
    reliable = reliable.sort_values("avg_no3_ppm", ascending=False).reset_index(drop=True)
    return reliable


if __name__ == "__main__":
    df = load_tudb()
    rollup = rollup_by_huc12(df)
    print(f"{len(rollup)} HUC12 watersheds with >= {MIN_OBSERVATIONS} observations\n")

    cols = [
        "huc12_name", "n_observations", "avg_no3_ppm", "pct_no3_impaired",
        "pct_phos_impaired", "bank_erosion_flags",
    ]
    print(rollup[cols].head(10).to_string(index=False))

    rollup.to_csv("outputs/watershed_rollup.csv", index=False)
    print("\nWrote outputs/watershed_rollup.csv")
