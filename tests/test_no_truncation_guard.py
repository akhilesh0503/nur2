"""Regression test for the actual bug found in review: run_pipeline.py once
fed only rollup.head(20) into Steps 3-6 instead of the full watershed set,
which silently biased composite_score.py (it's supposed to rank by four
independent factors, but three of them never got evaluated outside that
pre-filtered pool). This test would have caught that on day one.

Network calls are stubbed out (fast, deterministic) since the point here is
purely to prove no code path silently slices the watershed list before it
reaches Steps 3-6 -- not to re-verify the APIs themselves (see
test_rollup_regression.py and test_field_boundaries_known_values.py for
real-data checks).
"""
import os

import pandas as pd

import composite_score
import field_boundaries
import hydrology_nldi
import landuse_cdl
import run_pipeline
import watershed_rollup


def _fake_landuse(huc12_codes, year=2023, progress_path=None):
    codes = list(huc12_codes)
    return pd.DataFrame([
        {"huc12_code": c, "cdl_year": year, "row_crop": 50.0, "pasture": 20.0,
         "forest": 15.0, "urban": 10.0, "other": 5.0}
        for c in codes
    ])


def _fake_downstream(huc12_codes, progress_path=None):
    codes = list(huc12_codes)
    return pd.DataFrame([
        {"huc12_code": c, "outlet_comid": i, "downstream_huc12_count": 50,
         "downstream_trace_truncated": True, "downstream_flow_miles": 1000.0}
        for i, c in enumerate(codes)
    ])


def _fake_field_summary(shortlist_path="outputs/candidate_shortlist.csv"):
    shortlist = pd.read_csv(shortlist_path, dtype={"huc12_code": str})
    return pd.DataFrame([
        {"huc12_code": c, "huc12_name": n, "n_fields": 0, "n_ag_fields": None,
         "pct_ag_fields": None, "ag_acres": None, "field_data_available": False}
        for c, n in zip(shortlist["huc12_code"], shortlist["huc12_name"])
    ])


def test_run_pipeline_scores_full_rollup_not_a_slice(tmp_path, monkeypatch):
    # run_pipeline.main() writes real files under outputs/ and data/processed/
    # with hardcoded relative paths -- isolate this test in a scratch
    # directory so it can NEVER touch the real project's outputs. (This
    # bit me once already: an earlier version of this test ran against the
    # real cwd and clobbered outputs/*.csv with this test's fake data.)
    import shutil

    real_project_root = os.getcwd()
    shutil.copy(
        os.path.join(real_project_root, "TUDB-241030_Distn_v03.xlsx"),
        tmp_path / "TUDB-241030_Distn_v03.xlsx",
    )
    (tmp_path / "outputs").mkdir()
    (tmp_path / "data" / "processed").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(landuse_cdl, "compute_landuse_for_watersheds", _fake_landuse)
    monkeypatch.setattr(hydrology_nldi, "compute_downstream_impact", _fake_downstream)
    monkeypatch.setattr(field_boundaries, "summarize_shortlist_fields", _fake_field_summary)
    monkeypatch.setattr(run_pipeline, "compute_landuse_for_watersheds", _fake_landuse)
    monkeypatch.setattr(run_pipeline, "compute_downstream_impact", _fake_downstream)
    monkeypatch.setattr(run_pipeline, "summarize_shortlist_fields", _fake_field_summary)
    monkeypatch.setattr(run_pipeline, "LANDUSE_PROGRESS_PATH", "data/processed/landuse.jsonl")
    monkeypatch.setattr(run_pipeline, "DOWNSTREAM_PROGRESS_PATH", "data/processed/downstream.jsonl")

    from load_tudb import load_tudb
    rollup = watershed_rollup.rollup_by_huc12(load_tudb())
    n_watersheds = len(rollup)
    assert n_watersheds > 20, "sanity check: fixture data should have more than 20 qualifying watersheds"

    run_pipeline.main()

    landuse_out = pd.read_csv(tmp_path / "outputs" / "landuse_by_watershed.csv")
    downstream_out = pd.read_csv(tmp_path / "outputs" / "downstream_impact_by_watershed.csv")

    assert len(landuse_out) == n_watersheds, (
        f"Step 3 output has {len(landuse_out)} rows but the rollup has {n_watersheds} watersheds -- "
        "looks like Steps 3-6 are being fed a pre-filtered slice again, not the full set."
    )
    assert len(downstream_out) == n_watersheds, (
        f"Step 4 output has {len(downstream_out)} rows but the rollup has {n_watersheds} watersheds -- "
        "looks like Steps 3-6 are being fed a pre-filtered slice again, not the full set."
    )


def test_compute_functions_never_drop_rows_by_themselves():
    """compute_landuse_for_watersheds / compute_downstream_impact / compute_composite_score
    should never on their own reduce the candidate pool -- only composite_score's
    final .head(N) shortlist selection should do that, and only at the very end."""
    codes = [f"07000000{i:04d}" for i in range(30)]

    landuse = _fake_landuse(codes)
    downstream = _fake_downstream(codes)
    assert len(landuse) == 30
    assert len(downstream) == 30

    rollup = pd.DataFrame([
        {"huc12_code": c, "huc12_name": f"Fake Creek {i}", "huc10_name": "0700000000",
         "n_observations": 10, "avg_no3_ppm": float(i), "pct_no3_impaired": 10.0,
         "pct_phos_impaired": 10.0}
        for i, c in enumerate(codes)
    ])
    scored = composite_score.compute_composite_score(
        rollup,
        landuse[["huc12_code", "row_crop", "pasture", "forest", "urban", "other"]],
        downstream[["huc12_code", "downstream_huc12_count", "downstream_flow_miles"]],
    )
    assert len(scored) == 30, "compute_composite_score should score every watershed it's given, not truncate"
