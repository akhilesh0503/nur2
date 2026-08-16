"""Known-value checks for field_boundaries.py's per-state field index.

Skipped if a state's ACPF shapefile isn't already downloaded locally --
these are 200MB+ per-state downloads and a test suite shouldn't trigger
that on its own. Run the pipeline once (or field_boundaries.py directly)
to populate data/raw/acpf/<STATE>/ before these will run for real.
"""
import os

import pytest

from field_boundaries import fields_for_huc12

IA_SHP = "data/raw/acpf/IA/IowaFieldBoundaries2019.shp"
MN_SHP = "data/raw/acpf/MN/MinnesotaFieldBoundaries2019.shp"

KNOWN_IOWA_WATERSHEDS = {
    "070600040503": {"name": "Little Volga River", "n_fields": 431, "n_ag": 280},
    "070600040504": {"name": "Coulee Creek-Volga River", "n_fields": 455, "n_ag": 231},
    "070600040502": {"name": "Headwaters Volga River", "n_fields": 310, "n_ag": 195},
}


@pytest.mark.skipif(not os.path.exists(IA_SHP), reason="Iowa ACPF data not downloaded locally")
@pytest.mark.parametrize("huc12_code", list(KNOWN_IOWA_WATERSHEDS))
def test_known_iowa_watershed_field_counts(huc12_code):
    expected = KNOWN_IOWA_WATERSHEDS[huc12_code]
    fields = fields_for_huc12(huc12_code)
    assert len(fields) == expected["n_fields"]
    assert int((fields["isAG"] == 1).sum()) == expected["n_ag"]


@pytest.mark.skipif(not os.path.exists(MN_SHP), reason="Minnesota ACPF data not downloaded locally")
def test_trout_brook_field_count_is_plausible():
    """Not a fixed known-value check (MN data wasn't hand-verified as
    thoroughly as Iowa's) -- just guards against a degenerate result
    (0 fields, or 0%/100% ag) slipping through silently."""
    fields = fields_for_huc12("070400020902")
    assert 100 < len(fields) < 2000
    pct_ag = (fields["isAG"] == 1).mean()
    assert 0.05 < pct_ag < 0.95
