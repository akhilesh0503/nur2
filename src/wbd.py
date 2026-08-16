"""Shared USGS WBD REST helpers: HUC12 boundary geometry + state list, cached
to disk (data/raw/wbd_boundaries/{huc12}.json) so repeated pipeline runs and
different modules (landuse_cdl.py, field_boundaries.py) never re-fetch the
same watershed's WBD record.

The `states` field (confirmed live during planning: HUC12 070400020902 ->
"MN") is what lets field_boundaries.py route a watershed to the right ACPF
state dataset without a hand-maintained HUC10->state map.
"""
import json
import os

from shapely.geometry import shape

from net_utils import get_json_retry

WBD_QUERY_URL = "https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/6/query"
CACHE_DIR = "data/raw/wbd_boundaries"


def fetch_huc12_feature(huc12_code: str) -> dict:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{huc12_code}.json")
    if os.path.exists(cache_path):
        with open(cache_path) as f:
            return json.load(f)

    params = {
        "where": f"huc12='{huc12_code}'",
        "outFields": "huc12,name,states",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    data = get_json_retry(WBD_QUERY_URL, params=params, timeout=30)
    features = data.get("features", [])
    if not features:
        raise ValueError(f"HUC12 {huc12_code} not found in WBD service")

    feat = features[0]
    states_raw = feat["properties"].get("states") or ""
    result = {
        "geometry": feat["geometry"],
        "states": [s.strip() for s in states_raw.split(",") if s.strip()],
    }
    with open(cache_path, "w") as f:
        json.dump(result, f)
    return result


def fetch_huc12_boundary(huc12_code: str):
    return shape(fetch_huc12_feature(huc12_code)["geometry"])


def fetch_huc12_states(huc12_code: str) -> list:
    return fetch_huc12_feature(huc12_code)["states"]
