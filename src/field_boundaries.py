"""Step 6: identify specific fields likely contributing runoff within the
top candidate watersheds, using ACPF's pre-built state field-boundary
datasets (see context_doc.txt Section 6b).

Data source: USDA Ag Data Commons (Figshare-hosted). Confirmed via the
Figshare API that these are per-state ZIPs (Iowa = 211 MB) containing:
  - a shapefile of field polygons with an `isAG` flag (no ownership data --
    this is the doc's own privacy-preserving field layer, derived from
    de-identified FSA CLU data)
  - CropHistory/LandUse CSVs joined on the same field ID, with per-year
    majority crop codes that match the CDL legend from landuse_cdl.py

This is a much smaller download than the alternative (USDA NASS's national
Crop Sequence Boundaries file, ~3.76 GB for all of CONUS with no way to
fetch just one region) -- confirmed by a HEAD request during
implementation, not assumed.

IMPORTANT, confirmed empirically: each field's `FBndID` embeds its HUC12
code directly (e.g. "F070600040503_12" -> HUC12 070600040503), which lets
us filter to a candidate watershed's fields by string prefix instead of a
spatial join.

State routing uses wbd.fetch_huc12_states() (the authoritative `states`
field USGS's WBD service returns per HUC12) instead of a hand-maintained
HUC10->state map, so it doesn't silently miss border watersheds or need
manual updates when a new watershed lands in the shortlist.

Performance: fields_for_huc12() filters a per-state index
(load_state_field_index) built with ONE fiona pass over the state's
shapefile and cached to disk, rather than re-scanning the raw shapefile
per watershed -- a full scan of Iowa's 717,938-field shapefile takes
tens of seconds, which doesn't scale well if repeated per watershed
across a multi-state shortlist. field_details_for_huc12() still does a
targeted per-watershed scan (with crop history joined in) for the small
number of example exports that want full detail.

Privacy caveat (doc Section 6c): this pipeline outputs field GEOMETRY-
adjacent attributes (acreage, isAG, crop history) only. It does not and
cannot resolve field ownership -- that requires the human/partnership
outreach step in Section 6d.

Coverage: see STATE_DATASETS below for which states are currently wired
up. A watershed whose state isn't wired in is reported as "no field data"
rather than silently skipped (see summarize_shortlist_fields).
"""
import os
import re
import zipfile

import fiona
import pandas as pd

from net_utils import get_bytes_retry, get_json_retry
from wbd import fetch_huc12_states

RAW_DIR = "data/raw"
ACPF_CACHE_DIR = os.path.join(RAW_DIR, "acpf")
FBNDID_HUC12_RE = re.compile(r"^F(\d{12})_")

# state -> (Figshare article ID, zip filename, shapefile basename inside the ZIP)
# MN/WI schema confirmed identical to IA's (FBndID/Acres/isAG, same
# F<12-digit-huc12>_<n> ID format) before wiring in -- not assumed.
STATE_DATASETS = {
    "IA": {
        "figshare_article_id": "24854613",
        "zip_name": "IA_ACPFfields2019.zip",
        "shp_name": "IowaFieldBoundaries2019.shp",
    },
    "MN": {
        "figshare_article_id": "24854703",
        "zip_name": "MN_ACPFfields2019.zip",
        "shp_name": "MinnesotaFieldBoundaries2019.shp",
    },
    "WI": {
        "figshare_article_id": "24854718",
        "zip_name": "WI_ACPFfields2019.zip",
        "shp_name": "WisconsinFieldBoundaries2019.shp",
    },
}


def _figshare_download_url(article_id: str, filename: str) -> str:
    data = get_json_retry(f"https://api.figshare.com/v2/articles/{article_id}", timeout=30)
    for f in data["files"]:
        if f["name"] == filename:
            return f["download_url"]
    raise ValueError(f"{filename} not found in Figshare article {article_id}")


def fetch_state_fields(state: str) -> str:
    """Download + extract a state's ACPF field boundary ZIP (cached). Returns
    the directory containing the extracted shapefile/CSVs."""
    cfg = STATE_DATASETS[state]
    extract_dir = os.path.join(ACPF_CACHE_DIR, state)
    shp_path = os.path.join(extract_dir, cfg["shp_name"])
    if os.path.exists(shp_path):
        return extract_dir

    os.makedirs(extract_dir, exist_ok=True)
    url = _figshare_download_url(cfg["figshare_article_id"], cfg["zip_name"])

    zip_path = os.path.join(RAW_DIR, cfg["zip_name"])
    if not os.path.exists(zip_path):
        content = get_bytes_retry(url, timeout=300)
        with open(zip_path, "wb") as f:
            f.write(content)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    return extract_dir


def load_crop_history(extract_dir: str) -> pd.DataFrame:
    csv_path = None
    for fname in os.listdir(extract_dir):
        if fname.lower().endswith("landuse2014_2019.csv"):
            csv_path = os.path.join(extract_dir, fname)
    if csv_path is None:
        return pd.DataFrame(columns=["FBndID"])
    return pd.read_csv(csv_path)


def state_for_huc12(huc12_code: str) -> str:
    """Returns the first state (from WBD's authoritative per-HUC12 states
    list) that has a wired-up ACPF dataset, or None if none do."""
    for state_abbr in fetch_huc12_states(huc12_code):
        if state_abbr in STATE_DATASETS:
            return state_abbr
    return None


def load_state_field_index(state: str) -> pd.DataFrame:
    """One-time (cached) pass over a state's field shapefile, reading only
    FBndID/Acres/isAG (never touching geometry) and extracting huc12_code
    from FBndID. This is what makes filtering by watershed fast."""
    index_path = os.path.join(ACPF_CACHE_DIR, f"{state}_field_index.csv")
    if os.path.exists(index_path):
        return pd.read_csv(index_path, dtype={"huc12_code": str})

    extract_dir = fetch_state_fields(state)
    shp_path = os.path.join(extract_dir, STATE_DATASETS[state]["shp_name"])

    rows = []
    skipped = 0
    with fiona.open(shp_path) as src:
        for feat in src:
            props = feat["properties"]
            fid = props["FBndID"]
            m = FBNDID_HUC12_RE.match(fid)
            if not m:
                skipped += 1
                continue
            rows.append({
                "FBndID": fid,
                "huc12_code": m.group(1),
                "acres": props["Acres"],
                "isAG": props["isAG"],
            })
    if skipped:
        print(f"  {state}: {skipped} fields had an FBndID not matching "
              f"F<12-digit-huc12>_<n>, skipped from the index")

    index = pd.DataFrame(rows)
    os.makedirs(ACPF_CACHE_DIR, exist_ok=True)
    index.to_csv(index_path, index=False)
    return index


def fields_for_huc12(huc12_code: str) -> pd.DataFrame:
    """Fast: FBndID/acres/isAG for a watershed, via the cached per-state index."""
    state = state_for_huc12(huc12_code)
    if state is None:
        return pd.DataFrame()
    index = load_state_field_index(state)
    return index[index["huc12_code"] == huc12_code].reset_index(drop=True)


def field_details_for_huc12(huc12_code: str) -> pd.DataFrame:
    """Full per-field detail including crop history, via a targeted shapefile
    scan. Only used for the small number of example exports -- the shortlist
    summary uses the faster fields_for_huc12() above."""
    state = state_for_huc12(huc12_code)
    if state is None:
        return pd.DataFrame()

    extract_dir = fetch_state_fields(state)
    shp_path = os.path.join(extract_dir, STATE_DATASETS[state]["shp_name"])
    landuse = load_crop_history(extract_dir)

    prefix = f"F{huc12_code}_"
    rows = []
    with fiona.open(shp_path) as src:
        for feat in src:
            fid = feat["properties"]["FBndID"]
            if fid.startswith(prefix):
                rows.append({
                    "FBndID": fid,
                    "huc12_code": huc12_code,
                    "acres": feat["properties"]["Acres"],
                    "isAG": feat["properties"]["isAG"],
                })

    fields = pd.DataFrame(rows)
    if fields.empty:
        return fields
    return fields.merge(landuse, on="FBndID", how="left")


def summarize_shortlist_fields(shortlist_path: str = "outputs/candidate_shortlist.csv") -> pd.DataFrame:
    shortlist = pd.read_csv(shortlist_path, dtype={"huc12_code": str})
    summaries = []
    for _, row in shortlist.iterrows():
        fields = fields_for_huc12(row["huc12_code"])
        if fields.empty:
            summaries.append({
                "huc12_code": row["huc12_code"],
                "huc12_name": row["huc12_name"],
                "n_fields": 0,
                "n_ag_fields": None,
                "pct_ag_fields": None,
                "ag_acres": None,
                "field_data_available": False,
            })
            continue
        ag = fields[fields["isAG"] == 1]
        summaries.append({
            "huc12_code": row["huc12_code"],
            "huc12_name": row["huc12_name"],
            "n_fields": len(fields),
            "n_ag_fields": len(ag),
            "pct_ag_fields": round(100 * len(ag) / len(fields), 1),
            "ag_acres": round(ag["acres"].sum(), 0),
            "field_data_available": True,
        })
    return pd.DataFrame(summaries)


if __name__ == "__main__":
    summary = summarize_shortlist_fields()
    print(summary.to_string(index=False))
    summary.to_csv("outputs/field_summary_by_watershed.csv", index=False)
    print("\nWrote outputs/field_summary_by_watershed.csv")

    # Full field-level detail (acres/isAG/crop history, no ownership info)
    # for the top watershed with field data available, as a concrete example
    # of Step 6's output.
    top_covered = summary[summary["field_data_available"]].iloc[0] if summary["field_data_available"].any() else None
    if top_covered is not None:
        detail = field_details_for_huc12(top_covered["huc12_code"])
        out_path = f"outputs/fields_{top_covered['huc12_code']}.csv"
        detail.to_csv(out_path, index=False)
        print(f"Wrote {out_path} ({len(detail)} fields for {top_covered['huc12_name']})")
