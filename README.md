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
uvx ruff check src tests
uvx pyright --project pyrightconfig.json
```

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
