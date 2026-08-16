# NuR2 Watershed Nutrient-Runoff Targeting Pipeline

Turns a public citizen-science water-quality dataset into a ranked shortlist
of watersheds — and, for each shortlisted watershed, specific farm fields —
likely contributing agricultural nutrient runoff. Built for NuR2, a
watershed-sustainability venture; full project background, the source
dataset's structure, data-quality caveats, and the original 7-step plan are
in [`context_doc.txt`](context_doc.txt).

Steps 1-6 of that plan are implemented here as a reusable pipeline. Step 7
(ground-truthing, farmer outreach) is explicitly a human/field task — see
[Handing off to Step 7](#handing-off-to-step-7) below.

## Quick start

```
pip install -r requirements-dev.txt
python src/run_pipeline.py
python -m pytest -v
```

`run_pipeline.py` runs all six steps end to end. It's slow the first time
(roughly 30-45 minutes, mostly Steps 3-4 making a network call per
watershed) but fast on every rerun after — everything is cached to
`data/raw/` and `data/processed/` and the run is resumable if interrupted
(see [Resumability](#resumability) below).

## The pipeline

| Step | Script | What it does | External data source |
|---|---|---|---|
| 1 | `load_tudb.py` | Loads and cleans the raw TU/MobileH2O water-quality spreadsheet | none (local file) |
| 2 | `watershed_rollup.py` | Aggregates readings by HUC12 watershed, ranks by severity | none (local computation) |
| 3 | `landuse_cdl.py` | % row-crop/pasture/forest/urban per watershed | USDA Cropland Data Layer |
| 4 | `hydrology_nldi.py` | Downstream river connectivity and reach | USGS NLDI |
| 5 | `composite_score.py` | Combines severity + persistence + land use + downstream impact into a ranked shortlist | none (combines 2-4) |
| 6 | `field_boundaries.py` | Specific field boundaries + crop history for shortlisted watersheds | USDA ACPF field boundaries (Ag Data Commons) |

Both Step 3 and Step 6 also depend on `wbd.py` (USGS Watershed Boundary
Dataset — boundary polygons and the state(s) each HUC12 sits in) and
`net_utils.py` (shared HTTP retry/backoff, used by every network call in
the pipeline).

Outputs land in `outputs/`:

- `watershed_rollup.csv` — all 109 watersheds with ≥5 observations, ranked by severity
- `landuse_by_watershed.csv`, `downstream_impact_by_watershed.csv` — Step 3/4 detail for all 109
- `candidate_shortlist.csv` — the final ranked top 15
- `field_summary_by_watershed.csv` — field-level summary for the shortlist
- `weight_sensitivity.csv` — see [Composite score weights](#composite-score-weights)

## Current result

As of the last full run, the top of the shortlist is the Volga River basin
cluster in northeast Iowa (Little Volga River, Coulee Creek-Volga River,
Headwaters Volga River, North Branch Volga River — 74-82% row-crop
agriculture, strong evidence farms are the driver) alongside Trout Brook,
MN. Field-level data (specific fields, crop rotation history, no ownership
info) is available for all 15 shortlisted watersheds, currently covering
Iowa, Minnesota, and Wisconsin — see `field_boundaries.STATE_DATASETS` for
exactly which states are wired up; a watershed in an unwired state is
reported as "no field data" rather than silently dropped.

## Design decisions worth knowing about

**Composite score weights.** `context_doc.txt` doesn't specify exact
weights for the four scoring factors, so `composite_score.py` picked a
starting point and then retuned it once with evidence:
`weight_sensitivity.py` showed the downstream-impact factor has weak
discriminating power across the full 109-watershed set (every watershed's
NLDI trace hits the same fixed truncation horizon — see
`hydrology_nldi.py`'s docstring — so the coefficient of variation is only
~3.4%). Its weight was cut from 20% to 10% as a result; the freed weight
went to agricultural land (the factor most directly tied to NuR2's core
thesis). 10 of the current top 15 watersheds rank in the top 15 under
*every* weighting scheme tested (including equal weights and a
severity-heavy scheme) — that's the robust core worth trusting most; rerun
`python src/weight_sensitivity.py` to see the full breakdown, including
which watersheds are weight-dependent.

**Steps 3-6 run against the full watershed set, not a shortcut.** An
earlier version of this pipeline validated Steps 3-6 against only the top
20 watersheds by nitrate. That biased `composite_score.py`, which is
supposed to weigh four independent factors — three of them never got
evaluated outside that pre-filtered pool. Fixed by re-running against all
109; 4 of the (then) top 15 changed as a direct result.
`tests/test_no_truncation_guard.py` guards against this regressing.

**Land-use data source.** Originally used NASS CropScape
(`nassgeodata.gmu.edu`, hosted at George Mason University). That service
went unreachable for about an hour mid-run, so `landuse_cdl.py` now uses a
different USDA-hosted endpoint instead (the API behind USDA's own
CroplandCROS map viewer, on `pdi.scinet.usda.gov`) — confirmed to return
identical results before switching.

## Resumability

Steps 3 and 4 write progress incrementally to `data/processed/*.jsonl` (one
line per completed watershed) and skip anything already there on a rerun.
If a run is interrupted or a watershed's request fails after retries, it's
logged to the matching `*.errors.jsonl` file and the run continues — nothing
blocks on a single bad watershed. Rerunning `run_pipeline.py` picks up
exactly where it left off.

## Testing

```
python -m pytest -v
```

12 tests in `tests/`, covering:

- **`test_no_truncation_guard.py`** — the highest-value one: proves
  `run_pipeline.py` always feeds the *full* rollup into Steps 3-6, not a
  pre-filtered slice (the exact bug described above). Network calls are
  stubbed out; this is about the code path, not re-verifying the APIs.
- **`test_rollup_regression.py`** — pins Steps 1-2 to known-correct values
  (observation counts, the Little Volga River numbers, HUC12 zero-padding,
  the Volga basin top-3 cluster).
- **`test_field_boundaries_known_values.py`** — pins Iowa's field counts
  and sanity-checks Minnesota's; both skip if that state's dataset isn't
  downloaded locally (they're 200MB+ each, not something a test suite
  should trigger on its own).

## Requirements

`requirements.txt` covers the pipeline itself (pandas, requests, shapely,
pyproj, rasterio, fiona, openpyxl). `requirements-dev.txt` adds pytest.
Python 3.8+.

## Handing off to Step 7

Step 7 in the original plan — ground-truthing candidate fields, engaging
local TU chapters / state DNRs / NRCS offices for farmer introductions, and
deploying NuR2's own sensors before committing to infrastructure — is
explicitly a human/field task, not something this pipeline does. What it
hands off:

- `outputs/candidate_shortlist.csv` — the 15 watersheds to prioritize, and why
- `outputs/field_summary_by_watershed.csv` + `outputs/fields_<huc12>.csv` —
  specific fields within each, with acreage, a farmed/not-farmed flag, and
  crop rotation history
- **No ownership or contact information** — by design (see
  `context_doc.txt` Section 6c/6d). Public field-boundary data
  intentionally excludes who owns or farms each field; connecting a field
  to a landowner requires a local human step (county assessor records, NRCS
  field office relationships, or TU chapter contacts who already have
  relationships with landowners in these watersheds).
