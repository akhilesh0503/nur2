"""Step 3: land-use overlay for candidate watersheds using USDA's Cropland
Data Layer (CDL).

Originally built against NASS CropScape's GetCDLFile service
(nassgeodata.gmu.edu -- hosted at George Mason University, not USDA's own
infrastructure). That service went down for 45+ minutes mid-run (confirmed
via direct curl testing, not just our own request failures), so this now
uses USDA's own "Calculate Statistics" geoprocessing service instead
(pdi.scinet.usda.gov -- the backend behind USDA's CroplandCROS map viewer,
found by inspecting that viewer's JS bundle for its API calls). Confirmed
to produce equivalent pixel counts to the original raster-mask approach
(spot-checked against Little Volga River: Corn 55483 vs 55478, Soybeans
34988 vs 34986 pixels -- tiny differences from Web Mercator vs Albers
reprojection, not a real discrepancy).

This is also simpler than the original approach: USDA's server computes
the zonal histogram itself and returns pixel counts by CDL code directly,
so there's no raster download or local rasterio masking anymore -- just an
async job: submit the watershed polygon -> poll until done -> read the
histogram.

Crop-code -> category mapping is built from NASS's own authoritative
CDL_codes_names.xlsx (downloaded into data/raw/), not guessed, and bucketed
into the four categories context_doc.txt Step 3 asks for: row_crop,
pasture, forest, urban. Everything else (water, wetland, barren, orchards/
tree crops, shrubland, no-data) falls into "other".

compute_landuse_for_watersheds() supports an optional progress_path for
resumable runs against the full 109-watershed rollup (not just a pre-filtered
top-N slice -- running Steps 3-6 against a pool already pre-selected by
severity would bias composite_score.py's other three factors, see plan).
"""
import json
import os
import time

import openpyxl
import pandas as pd
from pyproj import Transformer

from net_utils import get_bytes_retry, get_json_retry, post_json_retry
from wbd import fetch_huc12_boundary

CDL_STATS_BASE = (
    "https://pdi.scinet.usda.gov/geoprocessing/rest/services/"
    "Calculate_Statistics_with_HI/GPServer/Calculate%20Statistics"
)
CDL_LEGEND_URL = "https://www.nass.usda.gov/Research_and_Science/Cropland/docs/CDL_codes_names.xlsx"

RAW_DIR = "data/raw"
LEGEND_PATH = os.path.join(RAW_DIR, "CDL_codes_names.xlsx")

DEFAULT_YEAR = 2023
JOB_POLL_INTERVAL_S = 1.5
JOB_MAX_WAIT_S = 90

# Category buckets built from the official code->name table (see
# load_cdl_legend). Matches doc Step 3's four requested categories; anything
# not row_crop/pasture/forest/urban is "other" (water, wetland, barren,
# orchards/tree crops, shrubland, no-data).
PASTURE_CODES = {36, 37, 58, 59, 60, 176}  # Alfalfa/Hay/Clover/Sod/Switchgrass/Grassland-Pasture
FOREST_CODES = {63, 141, 142, 143}
URBAN_CODES = {82, 121, 122, 123, 124}
# Row crop = every other numbered crop code in the legend (grains, oilseeds,
# vegetables, double crops, fallow/idle cropland) minus the buckets above and
# minus non-crop/natural/other codes handled by "other".
NON_ROWCROP_OTHER_CODES = {
    0, 64, 65, 66, 67, 68, 69, 70, 71, 72, 74, 75, 76, 77,  # barren/shrub/orchards/tree crops
    81, 83, 87, 88, 92, 111, 112, 131, 152, 190, 195,  # clouds/water/wetland/nonag/ice/barren/shrub
}


def load_cdl_legend(path: str = LEGEND_PATH) -> pd.DataFrame:
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        content = get_bytes_retry(CDL_LEGEND_URL, timeout=30)
        with open(path, "wb") as f:
            f.write(content)

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb["cdl_codes_names"]
    rows = [r for r in ws.iter_rows(values_only=True) if r[0] not in (None, "MasterCat") and r[1] is not None]
    legend = pd.DataFrame(rows, columns=["code", "name"])
    legend["code"] = legend["code"].astype(int)

    def categorize(code: int) -> str:
        if code in PASTURE_CODES:
            return "pasture"
        if code in FOREST_CODES:
            return "forest"
        if code in URBAN_CODES:
            return "urban"
        if code in NON_ROWCROP_OTHER_CODES:
            return "other"
        return "row_crop"

    legend["category"] = legend["code"].apply(categorize)
    return legend


def _submit_stats_job(polygon, year: int) -> str:
    to_merc = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    rings = [[list(to_merc.transform(x, y)) for x, y in polygon.exterior.coords]]
    aoi = {
        "geometryType": "esriGeometryPolygon",
        "spatialReference": {"wkid": 102100},
        "features": [{
            "geometry": {"rings": rings, "spatialReference": {"wkid": 102100}},
            "attributes": {"OBJECTID": 1},
        }],
    }
    params = {"AOI": json.dumps(aoi), "Dataset_Name": "CDLS_WM", "Year": str(year), "f": "json"}
    data = post_json_retry(f"{CDL_STATS_BASE}/submitJob", data=params, timeout=30)
    if "jobId" not in data:
        raise RuntimeError(f"submitJob failed: {data}")
    return data["jobId"]


def _poll_stats_job(job_id: str) -> None:
    url = f"{CDL_STATS_BASE}/jobs/{job_id}"
    waited = 0.0
    while waited < JOB_MAX_WAIT_S:
        data = get_json_retry(url, params={"f": "json"}, timeout=20)
        status = data.get("jobStatus")
        if status == "esriJobSucceeded":
            return
        if status == "esriJobFailed":
            raise RuntimeError(f"CalculateStatistics job failed: {data.get('messages')}")
        time.sleep(JOB_POLL_INTERVAL_S)
        waited += JOB_POLL_INTERVAL_S
    raise TimeoutError(f"CalculateStatistics job {job_id} did not complete within {JOB_MAX_WAIT_S}s")


def _fetch_stats_result(job_id: str) -> list:
    url = f"{CDL_STATS_BASE}/jobs/{job_id}/results/Statistics"
    data = get_json_retry(url, params={"f": "json"}, timeout=20)
    return data["value"]["histograms"][0]["counts"]


def zonal_landuse_via_api(polygon, legend: pd.DataFrame, year: int = DEFAULT_YEAR) -> dict:
    job_id = _submit_stats_job(polygon, year)
    _poll_stats_job(job_id)
    counts = _fetch_stats_result(job_id)

    code_to_category = dict(zip(legend["code"], legend["category"]))
    totals = {"row_crop": 0, "pasture": 0, "forest": 0, "urban": 0, "other": 0}
    for code, count in enumerate(counts):
        if count:
            totals[code_to_category.get(code, "other")] += count

    total_px = sum(totals.values())
    if total_px == 0:
        return {k: None for k in totals}
    return {k: round(100 * v / total_px, 1) for k, v in totals.items()}


def compute_landuse_for_watersheds(huc12_codes, year: int = DEFAULT_YEAR, progress_path: str = None) -> pd.DataFrame:
    """Compute land use for each huc12 code. If progress_path is given, results
    are appended there as they complete (one JSON line per watershed) and
    already-completed codes are skipped on a rerun -- makes this resumable
    for a long run against the full watershed set. Failures are logged to
    "<progress_path>.errors.jsonl" and don't stop the run."""
    legend = load_cdl_legend()
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
                polygon = fetch_huc12_boundary(code)
                landuse = zonal_landuse_via_api(polygon, legend, year)
                row = {"huc12_code": code, "cdl_year": year, **landuse}
            except Exception as e:
                if errors_path:
                    with open(errors_path, "a") as ef:
                        ef.write(json.dumps({"huc12_code": code, "stage": "landuse_cdl", "error": str(e)}) + "\n")
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

    landuse = compute_landuse_for_watersheds(
        rollup["huc12_code"], progress_path="data/processed/landuse_progress.jsonl"
    )
    merged = rollup.merge(landuse, on="huc12_code")

    cols = ["huc12_name", "avg_no3_ppm", "row_crop", "pasture", "forest", "urban", "other"]
    print(merged[cols].to_string(index=False))

    merged.to_csv("outputs/landuse_by_watershed.csv", index=False)
    print(f"\nWrote outputs/landuse_by_watershed.csv ({len(merged)}/{len(rollup)} watersheds)")
