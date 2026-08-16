"""Step 5: composite scoring -- combine severity, persistence, % agricultural
land, and downstream impact into a single ranked shortlist.

Each component is min-max normalized to 0-1 across the candidate set, then
combined with these weights (documented here since the doc doesn't specify
exact weights -- these are a defensible starting point, retuned once with
real evidence, see below):

  - severity (40%): avg nitrate, % nitrate-impaired, % phosphate-impaired
    readings -- averaged. Weighted highest since it's the direct evidence
    of a nutrient problem.
  - persistence (20%): observation count. More observations backing a
    watershed's severity numbers means less risk it's noise/a single storm
    event (context_doc.txt Section 4g).
  - agricultural land (30%): % row-crop land in the watershed (from CDL).
    Tests whether farms are a plausible source, per Step 3's purpose --
    a high-nitrate watershed with low row-crop % scores lower here since
    NuR2's edge-of-field capture model targets farm runoff specifically.
  - downstream impact (10%): downstream flow miles (from NLDI).

Downstream impact was originally 20%, cut to 10% after weight_sensitivity.py
showed it has weak discriminating power across the full 109-watershed set:
coefficient of variation of only ~3.4% (every watershed's NLDI trace hits
the same fixed truncation horizon, see hydrology_nldi.py's docstring), and
zeroing its weight out entirely only changes 2 of the top 15 watersheds.
It's not literally inert -- hence keeping a reduced weight rather than
dropping it -- but it shouldn't carry equal weight to the other three when
its actual signal is this narrow. The freed weight went to agricultural
land, the factor most directly tied to NuR2's core thesis (this is a
judgment call, not derived from the data the way the cut itself was).
10 of the current top 15 are "robust core" watersheds that rank in the top
15 under every weighting scheme weight_sensitivity.py tested (including
equal weights and severity-heavy) -- see outputs/weight_sensitivity.csv.
"""
import pandas as pd

WEIGHTS = {
    "severity": 0.40,
    "persistence": 0.20,
    "agricultural_land": 0.30,
    "downstream_impact": 0.10,
}
SHORTLIST_SIZE = 15


def _normalize(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(0.5, index=series.index)
    return (series - lo) / (hi - lo)


def compute_composite_score(rollup: pd.DataFrame, landuse: pd.DataFrame, downstream: pd.DataFrame) -> pd.DataFrame:
    df = rollup.merge(landuse, on="huc12_code", suffixes=("", "_lu"))
    df = df.merge(downstream, on="huc12_code", suffixes=("", "_ds"))

    severity_parts = pd.concat([
        _normalize(df["avg_no3_ppm"]),
        _normalize(df["pct_no3_impaired"]),
        _normalize(df["pct_phos_impaired"]),
    ], axis=1)
    df["severity_score"] = severity_parts.mean(axis=1)

    df["persistence_score"] = _normalize(df["n_observations"])
    df["agricultural_land_score"] = _normalize(df["row_crop"])
    df["downstream_impact_score"] = _normalize(df["downstream_flow_miles"])

    df["composite_score"] = (
        WEIGHTS["severity"] * df["severity_score"]
        + WEIGHTS["persistence"] * df["persistence_score"]
        + WEIGHTS["agricultural_land"] * df["agricultural_land_score"]
        + WEIGHTS["downstream_impact"] * df["downstream_impact_score"]
    )

    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


if __name__ == "__main__":
    rollup = pd.read_csv("outputs/watershed_rollup.csv", dtype={"huc12_code": str})
    landuse = pd.read_csv("outputs/landuse_by_watershed.csv", dtype={"huc12_code": str})
    downstream = pd.read_csv("outputs/downstream_impact_by_watershed.csv", dtype={"huc12_code": str})

    landuse = landuse[["huc12_code", "row_crop", "pasture", "forest", "urban", "other"]]
    downstream = downstream[["huc12_code", "downstream_huc12_count", "downstream_flow_miles"]]

    scored = compute_composite_score(rollup, landuse, downstream)
    shortlist = scored.head(SHORTLIST_SIZE)

    cols = [
        "huc12_name", "huc10_name", "n_observations", "avg_no3_ppm",
        "row_crop", "downstream_flow_miles", "composite_score",
    ]
    print(shortlist[cols].round(3).to_string(index=False))

    shortlist.to_csv("outputs/candidate_shortlist.csv", index=False)
    print(f"\nWrote outputs/candidate_shortlist.csv ({len(shortlist)} watersheds)")
