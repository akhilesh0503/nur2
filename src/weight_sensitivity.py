"""Sensitivity analysis for composite_score.py's weighting scheme.

context_doc.txt doesn't specify exact weights for the four composite-score
factors, so composite_score.py picked a defensible default (40% severity,
20% each for persistence/ag land/downstream impact). This script checks how
much that choice actually matters: which watersheds land in the top 15
regardless of the weights used (a "robust core" worth trusting), and which
only get there under one specific weighting (fragile, sensitive to a
somewhat arbitrary choice).

Also directly tests the finding from downstream_impact analysis: that
downstream_flow_miles has very low discriminating power across the full
109-watershed set (coefficient of variation ~3.4% -- every watershed's
trace hits the same fixed truncation horizon, see hydrology_nldi.py). If
dropping that factor's weight barely changes the shortlist, that's
independent confirmation it isn't doing much work in the current design.
"""
import pandas as pd

from composite_score import _normalize

WEIGHT_SCHEMES = {
    "original (40/20/20/20)": {"severity": 0.40, "persistence": 0.20, "agricultural_land": 0.20, "downstream_impact": 0.20},
    "equal (25/25/25/25)": {"severity": 0.25, "persistence": 0.25, "agricultural_land": 0.25, "downstream_impact": 0.25},
    "severity-heavy (55/15/15/15)": {"severity": 0.55, "persistence": 0.15, "agricultural_land": 0.15, "downstream_impact": 0.15},
    "no downstream (45/25/30/0)": {"severity": 0.45, "persistence": 0.25, "agricultural_land": 0.30, "downstream_impact": 0.0},
    "ag-land-heavy (30/15/45/10)": {"severity": 0.30, "persistence": 0.15, "agricultural_land": 0.45, "downstream_impact": 0.10},
}

SHORTLIST_SIZE = 15


def score_with_weights(rollup, landuse, downstream, weights) -> pd.DataFrame:
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
        weights["severity"] * df["severity_score"]
        + weights["persistence"] * df["persistence_score"]
        + weights["agricultural_land"] * df["agricultural_land_score"]
        + weights["downstream_impact"] * df["downstream_impact_score"]
    )
    return df.sort_values("composite_score", ascending=False).reset_index(drop=True)


def run_sensitivity_analysis() -> pd.DataFrame:
    rollup = pd.read_csv("outputs/watershed_rollup.csv", dtype={"huc12_code": str})
    landuse = pd.read_csv("outputs/landuse_by_watershed.csv", dtype={"huc12_code": str})
    downstream = pd.read_csv("outputs/downstream_impact_by_watershed.csv", dtype={"huc12_code": str})
    landuse = landuse[["huc12_code", "row_crop", "pasture", "forest", "urban", "other"]]
    downstream = downstream[["huc12_code", "downstream_huc12_count", "downstream_flow_miles"]]

    membership = {}
    for scheme_name, weights in WEIGHT_SCHEMES.items():
        scored = score_with_weights(rollup, landuse, downstream, weights)
        top15 = set(scored.head(SHORTLIST_SIZE)["huc12_code"])
        for code in top15:
            membership.setdefault(code, set()).add(scheme_name)

    names = rollup.set_index("huc12_code")["huc12_name"]
    rows = []
    for code, schemes in membership.items():
        rows.append({
            "huc12_code": code,
            "huc12_name": names.get(code, "?"),
            "n_schemes_in_top15": len(schemes),
            "in_all_schemes": len(schemes) == len(WEIGHT_SCHEMES),
            "missing_from": ", ".join(sorted(set(WEIGHT_SCHEMES) - schemes)) or None,
        })
    result = pd.DataFrame(rows).sort_values(
        ["n_schemes_in_top15", "huc12_name"], ascending=[False, True]
    ).reset_index(drop=True)
    return result


if __name__ == "__main__":
    result = run_sensitivity_analysis()
    n_robust = result["in_all_schemes"].sum()
    n_total = len(result)
    print(f"{n_robust} watersheds appear in the top 15 under ALL {len(WEIGHT_SCHEMES)} weight schemes tested")
    print(f"{n_total - n_robust} watersheds are weight-dependent (appear in some schemes but not others)\n")

    print("=== Robust core (top 15 regardless of weights) ===")
    print(result[result["in_all_schemes"]][["huc12_name", "n_schemes_in_top15"]].to_string(index=False))

    print("\n=== Weight-dependent (drop out under at least one scheme) ===")
    fragile = result[~result["in_all_schemes"]]
    print(fragile[["huc12_name", "n_schemes_in_top15", "missing_from"]].to_string(index=False))

    result.to_csv("outputs/weight_sensitivity.csv", index=False)
    print("\nWrote outputs/weight_sensitivity.csv")
