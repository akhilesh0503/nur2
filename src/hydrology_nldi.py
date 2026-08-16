"""Step 4: downstream connectivity tracing via USGS NLDI (Network Linked
Data Index), used instead of downloading the full NHDPlus dataset (see
plan's "REST APIs over bulk downloads" decision).

For each candidate HUC12:
  1. Resolve its outlet COMID via the `huc12pp` (HUC12 Pour Points) linked
     dataset -- confirmed this maps a HUC12 code straight to its NHDPlus
     outlet point and comid, no separate boundary/geometry math needed.
  2. Trace downstream via navigation/DM (downstream mainstem) to get:
     - total downstream flowline length (miles) -- summed with a geodesic
       (WGS84 ellipsoid) length calc, not naive Euclidean, since these are
       lon/lat coordinates.
     - count of downstream HUC12 pour points reached.

Caveat (verified during implementation, not assumed): the `distance` param
does NOT control how far the trace actually goes -- passing 9999, 99999,
and 500000 (km) all produced identical results for a test watershed, and
every one of the 20 candidate watersheds' downstream traces independently
terminates at the exact same HUC12 (080101000103, ~36.94N, the MO/KY
border on the Mississippi mainstem), regardless of starting point or
requested distance. So the NLDI service silently clamps the actual trace
to some fixed real-world extent well short of the Gulf (29N), not a
feature-count limit. The counts/miles below are therefore a *relative*
leverage-weighting factor comparable across these candidates (all measured
to the same downstream horizon), not an absolute distance-to-Gulf figure.
Flagging this explicitly rather than presenting it as exhaustive.

Every raw API response is cached to disk (data/raw/nldi_cache/) and all
requests go through net_utils's retry/backoff helper -- this module makes
3 network calls per watershed, which adds up fast when run against the
full ~109-watershed rollup instead of just a pre-filtered top slice.
compute_downstream_impact() supports an optional progress_path for
resumable runs, same pattern as landuse_cdl.py.
"""
import json
import os

import pandas as pd
from pyproj import Geod

from net_utils import get_json_retry

NLDI_BASE = "https://api.water.usgs.gov/nldi/linked-data"
GEOD = Geod(ellps="WGS84")
TRACE_DISTANCE_KM = 9999
COMMON_TRUNCATION_HUC12 = "080101000103"  # observed trace horizon, see module docstring

CACHE_DIR = "data/raw/nldi_cache"


def _cached_json(cache_key: str, url: str, params: dict) -> dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)
    data = get_json_retry(url, params=params, timeout=60)
    with open(cache_path, "w") as f:
        json.dump(data, f)
    return data


def get_outlet_comid(huc12_code: str) -> int:
    data = _cached_json(f"{huc12_code}_huc12pp", f"{NLDI_BASE}/huc12pp/{huc12_code}", params={"f": "json"})
    features = data["features"]
    if not features:
        raise ValueError(f"No NLDI huc12pp match for {huc12_code}")
    return features[0]["properties"]["comid"]


def downstream_huc12_count(huc12_code: str) -> tuple:
    data = _cached_json(
        f"{huc12_code}_downstream_huc12pp",
        f"{NLDI_BASE}/huc12pp/{huc12_code}/navigation/DM/huc12pp",
        params={"f": "json", "distance": TRACE_DISTANCE_KM},
    )
    features = data["features"]
    n = len(features)
    truncated = bool(features) and features[-1]["properties"]["identifier"] == COMMON_TRUNCATION_HUC12
    return n, truncated


def downstream_flow_miles(huc12_code: str) -> float:
    data = _cached_json(
        f"{huc12_code}_downstream_flowlines",
        f"{NLDI_BASE}/huc12pp/{huc12_code}/navigation/DM/flowlines",
        params={"f": "json", "distance": TRACE_DISTANCE_KM},
    )
    features = data["features"]

    total_m = 0.0
    for feat in features:
        coords = feat["geometry"]["coordinates"]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        total_m += GEOD.line_length(lons, lats)
    return total_m / 1609.34


def compute_downstream_impact(huc12_codes, progress_path: str = None) -> pd.DataFrame:
    """Same resumable pattern as landuse_cdl.compute_landuse_for_watersheds:
    if progress_path is given, already-completed codes are skipped on rerun
    and results are appended incrementally; failures go to
    "<progress_path>.errors.jsonl" and don't stop the run."""
    huc12_codes = list(huc12_codes)

    rows_by_code = {}
    progress_file = None
    if progress_path:
        if os.path.exists(progress_path):
            with open(progress_path) as f:
                for line in f:
                    if line.strip():
                        row = json.loads(line)
                        rows_by_code[row["huc12_code"]] = row
        progress_file = open(progress_path, "a")

    errors_path = f"{progress_path}.errors.jsonl" if progress_path else None

    try:
        for code in huc12_codes:
            if code in rows_by_code:
                continue
            try:
                comid = get_outlet_comid(code)
                n_downstream_huc12, truncated = downstream_huc12_count(code)
                miles = downstream_flow_miles(code)
                row = {
                    "huc12_code": code,
                    "outlet_comid": comid,
                    "downstream_huc12_count": n_downstream_huc12,
                    "downstream_trace_truncated": truncated,
                    "downstream_flow_miles": round(miles, 1),
                }
            except Exception as e:
                if errors_path:
                    with open(errors_path, "a") as ef:
                        ef.write(json.dumps({"huc12_code": code, "stage": "hydrology_nldi", "error": str(e)}) + "\n")
                continue
            rows_by_code[code] = row
            if progress_file:
                progress_file.write(json.dumps(row) + "\n")
                progress_file.flush()
    finally:
        if progress_file:
            progress_file.close()

    ordered_rows = [rows_by_code[c] for c in huc12_codes if c in rows_by_code]
    return pd.DataFrame(ordered_rows)


if __name__ == "__main__":
    rollup = pd.read_csv("outputs/watershed_rollup.csv", dtype={"huc12_code": str})

    downstream = compute_downstream_impact(
        rollup["huc12_code"], progress_path="data/processed/downstream_progress.jsonl"
    )
    merged = rollup.merge(downstream, on="huc12_code")

    cols = [
        "huc12_name", "avg_no3_ppm", "downstream_huc12_count",
        "downstream_trace_truncated", "downstream_flow_miles",
    ]
    print(merged[cols].to_string(index=False))

    merged.to_csv("outputs/downstream_impact_by_watershed.csv", index=False)
    print(f"\nWrote outputs/downstream_impact_by_watershed.csv ({len(merged)}/{len(rollup)} watersheds)")
