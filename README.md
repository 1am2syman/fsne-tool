# FSNE Power Query → Polars

Every query in `queries/` has a matching Python module in `src/`:

| Module | Inputs / dependencies |
| --- | --- |
| `db_landing.py` | Recursive Excel filenames containing `landing` or `Landing` |
| `history.py` | Recursive history Excel filenames starting with `20`; all sheets |
| `ph.py` | `PH_FG.xlsx`; all sheets |
| `run_rate_parent.py` | Excel filenames containing `sec_` or `Sec_`; `Sheet1` |
| `run_rate_town.py` | `run_rate_parent` |
| `sec_history.py` | Same secondary-sales source as `run_rate_parent` |
| `soh_db.py` | Filenames containing `shelflife` or `Shelflife`, parsed as CSV; `ph` and `run_rate_parent` |
| `starting_date.py` | Excel filenames containing `config` or `Config`; named Excel table `config_table` with `variable` / `value` columns |

## Upload and run in the browser

Start the app from the repository checkout:

```bash
uv sync
uv run python ui/build.py  # initial sample dashboard; see prerequisites below
uv run python -m server --port 8770
```

Open `http://127.0.0.1:8770/`. In **Onboarding**, select the five files, give the run a label, and choose **Start run**. The report date comes from `starting_date` in the configuration workbook's `config_table`; changing a filename does not change its date or contents. Upload slots accept arbitrary filenames and save them under the names the queries expect. Landing rates and hierarchy require the same worksheets/columns as the supplied samples. Configuration accepts `.xlsx`/`.xlsm`, landing `.xlsx`/`.xlsm`, secondary sales `.xlsb`/`.xlsx`/`.xlsm`, hierarchy `.xlsx`, and shelf life `.csv` (up to 1 GiB each).

**Runs** shows actual queued/running/completed/failed states and the current pipeline step. A completed run offers **Open results** and the full 30-column **Excel** download. **Log** shows validation and calculation errors. Runs and archive/restore choices survive page reloads and server restarts. Jobs execute one at a time; a job interrupted by a server restart is marked failed, and its uploaded inputs remain available on disk. Reselect the files to submit a corrected run. An upload failure keeps the browser's selected files for retry.

Each run is isolated under ignored `runs/RUN-0001/` with `input/`, `output/`, private `history/`, `index.html`, `meta.json` and `log.txt`. The server never writes to `sample_input/` or the bundled `history/`. Its history view combines bundled snapshots and previous successful runs, takes one version per report date (latest successful run wins), and excludes future snapshots. Exact batch identities retain manual mitigation labels from the same saved period. New periods/batches have no manual mitigation labels; they are not automatically classified as Actionable. Movement still measures the manually labeled Actionable cohort. Archive hides runs from the active list without deleting results or historical facts.

The current dashboard needs saved history for movement comparisons and a matching latest secondary-sales month; unsuitable inputs fail visibly instead of falling back to sample data. The static `ui/index.html` and static preview can display results, but uploads require the live server. The batch table is the top 400 stock rows; the Excel download contains all rows. Rotation stays at 2×, rates use all supplied months through the report date, and batch-key whitespace follows the source queries.

The server binds to localhost. For another device, use the `preview` helper's tailnet-only proxy mode. No authentication is included: keep the writable app on the trusted tailnet. Override `FSNE_RUNS_DIR` and `FSNE_HISTORY_DIR` with absolute paths when needed. A mounted URL such as `/fsne-app/` is supported.

## Generate the inferred final sample report

```bash
uv sync
uv run python -m src report --data-dir sample_input --output output/inferred_report.xlsx
# Verify original unadjusted formulas without overwriting adjusted outputs:
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
```

`src/report.py` infers the missing final calculation layer from the supplied sample, without reading reference data during generation. Its unadjusted baseline reproduces all 27,891 rows and 30 columns within documented floating-point tolerance. Production rates now apply seasonality-aware upper-tail winsorization, so rate-dependent outputs intentionally differ from the original reference. See [docs/inference.md](docs/inference.md) for original formulas and [docs/run-rates.md](docs/run-rates.md) for the adjustment policy, audits and DOH column.

This is sample-matched inference, not a verified translation of the missing original formulas. Historical measures shown in `measures.png` require prior-period inputs that are not supplied.

## Run individual translated queries

Install [uv](https://docs.astral.sh/uv/), then:

```bash
uv sync
uv run python -m src soh_db --data-dir data/current --output output/soh_db.parquet
uv run python -m src.history --history-dir data/history --output output/history.csv
uv run python -m src.run_rate_parent --data-dir data/current
# Equivalent installed entry point:
uv run fsne-queries starting_date --data-dir data/current
```

Use any query name from the table with `python -m src QUERY`. Individual scripts run as modules (`python -m src.ph`), not `python src/ph.py`.

Default layout (input files are not bundled):

```text
data/
  current/                 # replacement for Excel Table2.final_path
    PH_FG.xlsx
    sec_sales.xlsx
    landing_rates.xlsx
    shelflife.csv
    config.xlsx
  history/                 # replacement for Excel Table2.history_path
    2025-01.xlsx
output/
```

All relative input/output paths are resolved with `pathlib` against the repository root derived from `__file__`, independent of the working directory. Absolute overrides are also accepted. No Excel `Table2` or Windows drive paths are required. Outputs support CSV, Parquet and XLSX; omit `--output` to print a preview.

## Python use

```python
from pathlib import Path
from src.soh_db import build

stock = build(Path("data/current"))
```

Each query exposes `build(...)` for file loading and dependency resolution, and `transform(...)` for already-loaded Polars DataFrames. `soh_db.transform(source, hierarchy, rates)` takes its dependencies explicitly; `run_rate_town.transform(source)` accepts the parent-rate result.

## Transformation behavior

- Native, chained Polars lazy expressions perform filters, casts, text operations, aggregation, deduplication and joins. No pandas, Python row loops, `map_elements`, `map_rows`, or row-wise UDFs. Python iteration is confined to filesystem/workbook metadata, column expressions and the single header row.
- Run rates clamp negative sales **before** monthly aggregation, then conservatively winsorize isolated positive spikes after a leave-one-town-out product/month seasonal adjustment. Sparse histories and ambiguous peaks are preserved. The adjusted sum is divided by the **global** distinct month count; missing pair-months count as zero. Raw `sec_history` is unchanged. See [the complete policy](docs/run-rates.md).
- Headers are promoted once after source concatenation, matching the M scripts. Only `history` explicitly removes repeated header rows. Other sources containing repeated headers can therefore raise conversion errors, just as the original queries do; they are not silently cleaned.
- `ph` fills missing parents with material codes and keeps the first duplicate material. Files use sorted recursive paths for deterministic selection; M's unspecified folder/deduplication ordering may choose a different duplicate.
- `soh_db` uses left joins (including null-key matching). Its batch key lowercases town names without replacing spaces. `sec_history` additionally replaces spaces with underscores. These intentionally different source rules are preserved.
- Stock filtering preserves the original pre-cast literal comparison: CSV `"0"` is removed, but `"0.0"` is retained. The subsequent M comparison of a text cell to numeric `0` does not remove it.
- `history.batch_index_short` removes the two rightmost hyphen-delimited suffixes. `Expiry Date` remains an integer in `history`, but is a date in `soh_db`.
- Numeric conversions are strict; Int64 conversions use half-to-even rounding. Nulls are retained. Dates accept ISO dates/timestamps, US month/day dates, `DD-Mon-YYYY` / `DD-Mon-YY`, `Mon-YYYY` (first of month), and Excel serial days (1899-12-30 epoch). Invalid non-null dates raise errors. The M scripts omit culture; ambiguous dates use **en-US**, which may differ from your Excel locale.

## Excel boundary and limitations

Polars' Calamine reader handles worksheet/table data. `openpyxl` is used only to discover named tables in modern workbooks, not to transform rows. Legacy `.xls` / `.xlsb` worksheets are supported by Calamine; `config_table` discovery requires `.xlsx` / `.xlsm`. Cached formula values must be available: Python does not recalculate Excel formulas.

`db_landing` reads sheets plus named table ranges because its M source does not filter workbook `Kind`; named tables are expanded by `Column1`…`Column5` field names as in M (tables with other field names contribute nulls and are filtered out). Exotic named ranges, Excel-specific error cells, quoted hyphens inside history batch identifiers, and 1904-date-system numeric serials are not emulated. Inputs containing these need workbook-specific validation. The date epoch and locale can be adjusted in `src/io.py`.

Sample inputs and a final reference report are supplied in `sample_input/` and `sample_output/`. Tests cover synthetic boundaries and generated Excel/CSV integration; `src.compare_report` checks the complete real sample and Excel serialization. No independent second-period reference or original final formulas are available.

## Verify

```bash
uv run python -m unittest discover -s tests -v
uvx ruff check src tests ui server
uvx pyright --project pyrightconfig.json
```

## Rebuild generated outputs and the UI

Only the final report files in `output/` (`inferred_report.*`, `comparison.*`, `current_measures.json`) and the sources are tracked.
Everything else is regenerable and gitignored: the other `output/*` files, `output/legacy-compare/`, `ui/index.html` and `validation-artifacts/`.

```bash
# 1. movement history (inputs of ui/build.py), from the saved reports in history/
uv run python -m src movement_series  --history-dir history --output output/movement_series.parquet
uv run python -m src movement_parents --history-dir history --output output/movement_parents.parquet
# 2. adjusted report + run-rate/transfer/sales-push outputs + ui/index.html
uv run python ui/build.py
# 3. publish the preview
cp ui/index.html ~/previews/fsne-ui/index.html
# optional: parity of the original unadjusted formulas (separate output dir)
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
```

`ui/build.py` also writes `run_rate_audit.parquet`, `run_rate_flags.csv`, `run_rate_impact.json`, `run_rates.parquet`,
`legacy_report.parquet`, `transfer_comparison.parquet` and `current_measures.json`. See `docs/run-rates.md`.

## total-stock-movement (LM vs CM, by brand)

```bash
uv run python -m src movement --data-dir sample_input --history-dir data/history --output output/movement.parquet
uv run python -m src movement_series --history-dir history --output output/movement_series.parquet   # last 6 months, every brand
uv run python ui/build.py   # embeds the series in ui/index.html (brand x month heatmap)
```

`src/movement.py` is fully columnar Polars (joins, one `group_by` per side, no row loops; ~40 ms for 27,891 batches).
`Reduced from LM = LM Stock BDT - CM Closing Stock BDT (No New Inclusions)`; `Reduction % = Reduced / LM`.
A CM batch is a *new inclusion* when its key (`batch_index` minus its last two hyphen segments) is absent from LM.
LM is the saved report in `--history-dir` (parquet/csv/xlsx with the report columns) with the latest `report_date` before the current one.

Not verifiable yet: no LM report or mitigation source exists in `sample_input/`. The mitigation file schema
(`batch_index`, `mitigation_status_category`, `remarks`, file name containing "mitigation") is an assumption.
Tests (`tests/test_movement.py`) use small hand-computed cases only.
