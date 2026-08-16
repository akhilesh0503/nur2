"""Step 1: load and clean the TUDB sheet from the Driftless Area dataset.

Source: TUDB-241030_Distn_v03.xlsx, sheet "TUDB", header at row 7.
Drops the broken "NO3 Bin (5n1)" column (see context_doc.txt Section 4a);
keeps "NO3 Bin (2n1)" as the usable nitrate reading.
"""
import pandas as pd

SOURCE_XLSX = "TUDB-241030_Distn_v03.xlsx"
SHEET_NAME = "TUDB"
HEADER_ROW = 6  # 0-indexed -> row 7 in the raw sheet

COLUMN_MAP = {
    "Observation Date-Local": "obs_date",
    "Year": "year",
    "Month No.": "month",
    "Monitoring Site": "monitoring_site",
    "Longitude": "longitude",
    "Latitude": "latitude",
    "ALK Bin (ppm)": "alkalinity_ppm",
    "HRD Bin (ppm)": "hardness_ppm",
    "pH Bin": "ph",
    "NO2 Bin (2n1) (ppm)": "no2_ppm",
    "NO3 Bin (2n1) (ppm)": "no3_ppm",
    "Orthophosphate (ppb)": "orthophosphate_ppb",
    "Water Temperature (oF)": "water_temp_f",
    "Fish Barrier": "fish_barrier",
    "Bank Erosion": "bank_erosion",
    "Trash": "trash",
    "Pipe/Drain Outflow": "pipe_drain_outflow",
    "Livestock in Water": "livestock_in_water",
    "Algal Bloom": "algal_bloom",
    "Fish Kill": "fish_kill",
    "Water Level": "water_level",
    "Clarity": "clarity",
    "Primary Stream": "primary_stream",
    "All Streams": "all_streams",
    "Trout Stream Flag": "trout_stream_flag",
    "Brook Trout Stream Flag": "brook_trout_stream_flag",
    "TU Chapter Area": "tu_chapter_area",
    "Observer Chapter Affiliation": "observer_chapter_affiliation",
    "HUC12 Code": "huc12_code",
    "HUC12 Name": "huc12_name",
    "HUC10 Name": "huc10_name",
}

NUMERIC_COLS = [
    "alkalinity_ppm", "hardness_ppm", "ph", "no2_ppm", "no3_ppm",
    "orthophosphate_ppb", "water_temp_f", "fish_barrier", "bank_erosion",
    "trash", "pipe_drain_outflow", "livestock_in_water", "algal_bloom",
    "fish_kill",
]


def load_tudb(path: str = SOURCE_XLSX) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=SHEET_NAME, header=HEADER_ROW)
    df = raw[list(COLUMN_MAP.keys())].rename(columns=COLUMN_MAP)

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Excel/pandas reads HUC12 Code as an integer, dropping the leading zero
    # (all Upper Mississippi HUC12s start with "07"). Zero-pad back to 12 digits.
    df["huc12_code"] = (
        df["huc12_code"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )
    df = df[df["huc12_code"].notna() & (df["huc12_code"] != "nan")]
    df["huc12_code"] = df["huc12_code"].str.zfill(12)

    return df.reset_index(drop=True)


if __name__ == "__main__":
    df = load_tudb()
    print(f"Loaded {len(df)} observations with a HUC12 code")
    print(f"NO3 (2n1) valid readings: {df['no3_ppm'].notna().sum()}")
    print(f"Distinct HUC12 watersheds: {df['huc12_code'].nunique()}")
