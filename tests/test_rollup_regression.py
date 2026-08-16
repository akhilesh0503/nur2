"""Regression tests for Steps 1-2 against known-correct values.

These numbers were hand-verified against context_doc.txt's Section 5 table
during the original build, after tracking down two real bugs: pandas
silently dropping the leading zero off HUC12 codes, and the phosphate
"impaired" threshold needing to come from the source file's own Scorecard
bins (100 ppb) rather than its "Poor" bin label (500 ppb, which produced
near-zero percentages). If either regresses, these numbers will drift.
"""
import pandas as pd

from load_tudb import load_tudb
from watershed_rollup import rollup_by_huc12


def _rollup():
    return rollup_by_huc12(load_tudb())


def test_total_observation_count():
    df = load_tudb()
    assert len(df) == 2319


def test_no3_2n1_valid_reading_count():
    df = load_tudb()
    assert df["no3_ppm"].notna().sum() == 1880


def test_huc12_codes_are_twelve_digit_zero_padded():
    df = load_tudb()
    codes = df["huc12_code"]
    assert (codes.str.len() == 12).all(), "found a HUC12 code that isn't exactly 12 digits"
    assert codes.str.match(r"^\d{12}$").all(), "found a HUC12 code with non-digit characters"
    assert (codes.str.startswith("0")).any(), (
        "expected at least some Upper-Mississippi-basin HUC12s to start with a leading zero -- "
        "if none do, the zero-padding fix may have regressed"
    )


def test_watershed_count_with_min_observations():
    rollup = _rollup()
    assert len(rollup) == 109


def test_little_volga_river_matches_context_doc_section_5():
    rollup = _rollup()
    row = rollup[rollup["huc12_code"] == "070600040503"].iloc[0]
    assert row["huc12_name"] == "Little Volga River"
    assert row["n_observations"] == 14
    assert row["avg_no3_ppm"] == 12.5
    assert row["pct_no3_impaired"] == 64.0
    assert row["pct_phos_impaired"] == 86.0
    assert row["bank_erosion_flags"] == 11


def test_top_three_are_the_volga_river_basin_cluster():
    rollup = _rollup()
    top3_names = rollup.sort_values("avg_no3_ppm", ascending=False).head(3)["huc12_name"].tolist()
    assert top3_names == ["Little Volga River", "Coulee Creek-Volga River", "Headwaters Volga River"]
