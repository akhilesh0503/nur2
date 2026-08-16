"""End-to-end pipeline runner: Steps 1-6 in order.

Steps 3-4 run against the FULL watershed rollup (all HUC12s with >= 5
observations), not a pre-filtered top-N slice -- composite_score.py ranks by
four independent factors (severity, persistence, ag land, downstream
impact), so pre-filtering by severity alone before computing the other
three would bias the result (see plan). Both steps write progress
incrementally to data/processed/*.jsonl and are resumable: killing this
mid-run and rerunning it picks up where it left off instead of re-fetching
already-completed watersheds.

This is a long-running job (~30-45 min for all 109 watersheds from cold
cache; near-instant if data/processed/*.jsonl already has everything).
"""
import pandas as pd

from load_tudb import load_tudb
from watershed_rollup import rollup_by_huc12
from landuse_cdl import compute_landuse_for_watersheds
from hydrology_nldi import compute_downstream_impact
from composite_score import compute_composite_score
from field_boundaries import summarize_shortlist_fields

LANDUSE_PROGRESS_PATH = "data/processed/landuse_progress.jsonl"
DOWNSTREAM_PROGRESS_PATH = "data/processed/downstream_progress.jsonl"


def main():
    print("Step 1-2: loading TUDB and rolling up by HUC12...")
    observations = load_tudb()
    rollup = rollup_by_huc12(observations)
    rollup.to_csv("outputs/watershed_rollup.csv", index=False)
    print(f"  {len(rollup)} watersheds with >= 5 observations\n")

    print(f"Step 3: land-use overlay for all {len(rollup)} watersheds...")
    landuse = compute_landuse_for_watersheds(rollup["huc12_code"], progress_path=LANDUSE_PROGRESS_PATH)
    rollup.merge(landuse, on="huc12_code").to_csv("outputs/landuse_by_watershed.csv", index=False)
    print(f"  {len(landuse)}/{len(rollup)} watersheds succeeded "
          f"(see {LANDUSE_PROGRESS_PATH}.errors.jsonl for any failures)\n")

    print(f"Step 4: downstream tracing for all {len(rollup)} watersheds...")
    downstream = compute_downstream_impact(rollup["huc12_code"], progress_path=DOWNSTREAM_PROGRESS_PATH)
    rollup.merge(downstream, on="huc12_code").to_csv("outputs/downstream_impact_by_watershed.csv", index=False)
    print(f"  {len(downstream)}/{len(rollup)} watersheds succeeded "
          f"(see {DOWNSTREAM_PROGRESS_PATH}.errors.jsonl for any failures)\n")

    print("Step 5: composite scoring...")
    scored = compute_composite_score(
        rollup,
        landuse[["huc12_code", "row_crop", "pasture", "forest", "urban", "other"]],
        downstream[["huc12_code", "downstream_huc12_count", "downstream_flow_miles"]],
    )
    shortlist = scored.head(15)
    shortlist.to_csv("outputs/candidate_shortlist.csv", index=False)
    print(f"  wrote outputs/candidate_shortlist.csv ({len(shortlist)} watersheds, "
          f"scored against all {len(scored)} that had complete data)\n")

    print("Step 6: field boundaries for shortlist watersheds...")
    field_summary = summarize_shortlist_fields()
    field_summary.to_csv("outputs/field_summary_by_watershed.csv", index=False)
    n_covered = field_summary["field_data_available"].sum()
    print(f"  field data available for {n_covered}/{len(field_summary)} shortlist watersheds "
          f"(see field_boundaries.py STATE_DATASETS for which states are wired up)\n")

    print("Pipeline complete. Outputs in outputs/:")
    for name in [
        "watershed_rollup.csv", "landuse_by_watershed.csv",
        "downstream_impact_by_watershed.csv", "candidate_shortlist.csv",
        "field_summary_by_watershed.csv",
    ]:
        print(f"  outputs/{name}")


if __name__ == "__main__":
    main()
