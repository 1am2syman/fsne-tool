# FSNE Power Query → Polars

Every query in `queries/` has a matching Python module in `src/`:

| Module | Inputs / dependencies |
| --- | --- |
| `history.py` | Recursive history Excel filenames starting with `20`; all sheets |
| `ph.py` | `PH_FG.xlsx`; all sheets |
| `run_rate_parent.py` | Excel filenames containing `sec_` or `Sec_`; `Sheet1` |
| `run_rate_town.py` | `run_rate_parent` |
| `sec_history.py` | Same secondary-sales source as `run_rate_parent` |
| `soh_db.py` | Filenames containing `shelflife` or `Shelflife`, parsed as CSV; `ph` and `run_rate_parent` |
| `starting_date.py` | Excel filenames containing `config` or `Config`; named Excel table `config_table` with `variable` / `value` columns |

## Upload and run in the browser

### First installation on Windows

Install [Git for Windows](https://git-scm.com/install/windows) and [uv](https://docs.astral.sh/uv/getting-started/installation/), then open a new Command Prompt in the folder where you want to install the app and run:

```cmd
git clone https://github.com/1am2syman/fsne-tool.git
cd fsne-tool
start-fsne.bat
```

The first launch needs internet access to install the locked dependencies with uv. For later launches, double-click `start-fsne.bat` in the `fsne-tool` folder.

On **64-bit Windows**, double-click `start-fsne.bat` inside the repository folder. It invokes `runtime\windows-x64\python.exe -B start.py --port 8770 --open-browser %*` directly—no PowerShell extraction or Python downloads are needed at startup. On first launch (or whenever `uv.lock` changes after an update), `start.py` uses your installed `uv` to install the locked packages into the ignored `.fsne-packages/` folder without downloading Python; subsequent launches start offline without invoking `uv`. See [runtime/README.md](runtime/README.md). Extra arguments passed to `start-fsne.bat` (for example `--port 8771`) are forwarded to `start.py`. Keep the command window open while using the app; close it to stop the server. Port 8770 must be available. A fresh checkout opens Monthly upload without calculating sample reports, so startup does not depend on matching sample inputs to saved history.

To update on Windows, close the FSNE launcher window and double-click `update.bat` inside the cloned repository. It checks that no Python process is running from `runtime\`, clears any stale `.git\index.lock` when Git is not running, runs `git fetch --prune`, sets aside local edits in Git stash before resetting to `@{u}` with `git reset --hard`, and cleans only the code directories (`src`, `server`, `ui`). Cleanup preserves Python cache directories (`__pycache__/`) and bytecode (`*.pyc`, `*.pyo`), which Windows can keep open after the app exits; obsolete source files and generated UI output are still removed. Ignored local directories outside those code directories, such as `.cache/` and `.fsne-packages/`, are never deleted. The retired `vendor/` runtime folder is ignored and excluded from stash cleanup, including when updating an older checkout. Run `start-fsne.bat` again afterward. A Git checkout and access to its remote are required.

When installing the cache-cleanup fix with an older `update.bat`, the old cleanup command may still ask `Deletion of directory 'server/__pycache__/' failed. Should I try again?` during that first update. Answer **n** for each cache-directory prompt, let the updater finish, then run `update.bat` again. The first run replaces the batch file before cleanup; the next run uses the cache exclusions. An already-running batch file cannot pick up the new command. No manual cache deletion is needed.

If an older updater repeatedly asks `Deletion of directory 'vendor/' failed. Should I try again?`, answer **n** and stop the attempt with **Ctrl+C** if it keeps prompting. Close the FSNE launcher and any terminals or Explorer windows open inside `vendor/`. For an untracked leftover `vendor/` folder, run `echo /vendor/>>.git/info/exclude` from Command Prompt in the repository folder, then retry `update.bat`; this local exclusion keeps the old updater from stashing and deleting that folder. It does not change tracked files. A failed stash cleanup may already have saved local files: inspect them with `git stash list`. LF-to-CRLF warnings are informational; command-like filenames in those warnings are stray local files being saved, not commands the updater is executing.

By default, user runs and settled history live outside the repository under `Documents/FSNE/runs` and `Documents/FSNE/history` (using the Windows Documents known folder on Windows), so repository updates never overwrite user data. On first launch, a new external `history/` folder is seeded with the baseline snapshots shipped in the repository's `history/` folder. You can inspect or change both folders live from the **Storage** page in the sidebar (saved to `Documents/FSNE/settings.json`), or lock them at startup with `FSNE_RUNS_DIR` and `FSNE_HISTORY_DIR` (existing in-repo `runs/` folders can be pointed to directly or moved to `Documents/FSNE/runs`).

### Upgrading from in-repository data folders

Before updating an existing installation, close the app and **copy the complete `runs/` and `history/` folders to a safe location outside the repository**. Preserve every run's subfolders, metadata and outputs, and all history files, including locally settled months. The updater resets tracked files to upstream, so local history commits alone are not a backup.

For the new defaults, copy the folders to `FSNE/runs` and `FSNE/history` inside your actual Documents folder (which may be redirected to OneDrive on Windows). Copy into new folders rather than overwriting another installation's data. Keep the original folders or a separate backup until you have verified the copied data. Then update and start the app, confirm the paths on the **Storage** page, and check your run list, saved results and settled months. If you copied elsewhere, select those folders on Storage or set `FSNE_RUNS_DIR` and `FSNE_HISTORY_DIR` before starting. Changing a storage path selects that folder; it does not move or merge its contents. An existing external history folder is not replaced by baseline seeding.

Windows runs use private copies of history. On Linux, older runs may contain symlinks into the original checkout: when copying those runs, dereference the links so the backup contains the actual files before removing or changing their targets. Move or back up Excel and Parquet history files together. Existing Excel-only months remain readable.

Alternatively, start the app from the repository checkout:

```bash
uv sync
uv run python -m server --port 8770
```

Open `http://127.0.0.1:8770/`. In **Monthly upload**, select the three source files, optionally name the run and choose **Start run**. The only period input is **FSNE month** (month and year). Uploading secondary sales detects it from the latest `Month-Year` in the workbook **plus one month**: August 2026 sales sets September 2026 FSNE. Calculations use the last calendar day of the FSNE month; the sales file must end in the previous month. There are no separate PH, stock or secondary-sales date inputs. You can choose the FSNE month before uploading, or import it from `config_table.starting_date` under **Import settings from Excel**. An explicit choice is checked against uploaded sales; replacing sales updates an automatically detected month. Settings and accepted uploads survive reloads. Restart the app server and refresh the browser after updating this logic; completed runs retain their frozen dates and results.

Stock value in BDT comes from the shelf-life report's `DB Landing value` column; no landing-rate workbook is needed (see [docs/stock-valuation.md](docs/stock-valuation.md)). Upload slots accept arbitrary filenames and save them under the names the queries expect. Each slot has a small sample-download button with synthetic rows in the accepted schema, including the optional configuration import. Sample downloads also work in saved dashboards. Secondary sales `.xlsb`/`.xlsx`/`.xlsm`, hierarchy `.xlsx`, shelf life `.csv`, and optional configuration `.xlsx`/`.xlsm` (up to 1 GiB each).

**Ongoing runs** shows unsettled work, including drafts, queued/running/completed/failed states and the current pipeline step. **Settled months** holds the official monthly results, with access to the final view, action plan input file, log and Reopen. Settled records appear here even if an older archive flag is present. **Archived runs** holds earlier or abandoned attempts, with results, downloads and logs available; restore an attempt to continue working, or explicitly delete it to free storage. These views do not move run folders. Completed runs open as **Pre-action plan view**, **Action plan view (not settled)** or **Final view** according to their stored lifecycle state. A flag on every dashboard tab identifies the view; provisional flags include carried and unlabelled batch counts. **Action plan input file** downloads the full frozen report plus four planning columns. The file is named `action_plan_to_tm_YYYY_MM_DD.xlsx` using the app machine's local date when Start run is accepted; downloading it later keeps that date. Older exports are upgraded on first download. Only `action_plan` and `mitigation_status` cells are unlocked, and mitigation statuses have a dropdown. **Log** shows validation and calculation errors. Run metadata and accepted inputs survive reloads and server restarts. Jobs execute one at a time; an interrupted job is marked failed with its inputs retained.

The planning columns are appended in this order: `lm_action_plan`, `lm_mitigation_status`, `action_plan`, `mitigation_status`. The LM values come from the latest settled report date in the previous calendar month relative to the **report date**, using an exact `batch_index` left join. Current row order and all current rows are retained, unmatched LM values are blank, and history-only rows are excluded. Duplicate or null history keys fail visibly rather than multiplying report rows. Legacy history column names are accepted. The two current-entry columns start blank, except uncovered batches reached by a brand, SKU or town plan set in the action plan pages (rescue, rebalance, loading-watch; [docs/action-plan-pages.md](docs/action-plan-pages.md)); those notes pre-fill them and the file is rebuilt on the next download. Start run stops if the previous month has no settled history; an explicit confirmation overrides this with empty LM columns and no carried statuses. Existing workbooks in shared `history/` count as settled without run metadata.

**Upload action plan** accepts the workbook returned by sales, with `lm_` columns still present. It first checks the exact batch set, uniqueness, required columns and report date, then shows matched, blank and unrecognised counts before acceptance. Mitigation statuses have leading and trailing whitespace (including non-breaking spaces) removed before validation, and the cleaned values are saved on acceptance. Blank remarks/statuses and genuinely unrecognised statuses are warnings; free-text remark spacing is retained. Only the two entry columns join onto the frozen report; altered report numbers are ignored. Acceptance replaces the earlier upload and rebuilds snapshot, movement and dashboard data without recalculating the report or writing shared history.

**Settle month** confirms month, batches and missing statuses. Settlement without an upload needs an additional explicit confirmation and exports carried-over statuses. It writes the frozen monthly record as `YYYY_MM_DD.parquet` and its Excel export as `YYYY_MM_DD.xlsx` to the configured history directory (`Documents/FSNE/history` by default) using the report date and archives other runs for that calendar month. When `FSNE_HISTORY_DIR` points inside a Git repository, settlement also commits only that month's history changes locally (and removes any previous export for that month in the same commit), never pushing and leaving unrelated staged files untouched; a missing git executable, missing identity or other commit failure leaves the snapshot settled and exported, visibly marked **settled, not committed**. **Reopen** confirms removal of the export (in a new local commit when inside a Git repo), returns the run to Ongoing runs in its uploaded/provisional state, and marks later dependent runs out of date. Anyone using the app may perform these actions.

New calculations save both `history/snapshot.parquet` (the frozen monthly data with carried-over or accepted action-plan values) and its Excel export inside each run. **Settle month** publishes both `YYYY_MM_DD.parquet` and `YYYY_MM_DD.xlsx` to the configured shared history folder. History readers prefer the Parquet record when both exist. If a user brings Excel-only history, the first read parses the complete workbook and stores a matching `.parquet` beside it, including when the caller only asks for one column. Later reads and runs reuse that file instead of parsing Excel again. A run that copies history also repairs the shared source first, then freezes both formats before queueing. The history folder must be writable for this remedy; a failed conversion leaves the workbook untouched and publishes no partial Parquet. Replacing a settlement or reopening a month removes both shared files while preserving existing runs’ private copies. Keep both files together when moving or backing up history; edit returned action plans through the upload flow, since changing the Excel export alone does not update its Parquet record. Existing Excel-only months acquire their missing Parquet record when first consumed, without reopening, recalculating or repricing the month. The conversion preserves the same values and types returned by the existing Excel reader. Supplied Parquets are reused as authoritative records and are not regenerated from Excel.

After updating this version, restart the app server and refresh the browser to load the storage behavior and current UI.

Each run is isolated under `<FSNE_RUNS_DIR>/RUN-0001/` (`Documents/FSNE/runs/RUN-0001/` by default) with `input/`, `output/`, `history-source/`, private `history/`, `data.json`, `meta.json` and `log.txt`. Start run freezes copies of settled prior months before queueing. The run's frozen `history/snapshot.parquet` is authoritative when present, with `history/snapshot.xlsx` retained for Excel workflows and older runs; the shared history files are its published records. Movement carries previous-calendar-month labels by its shortened batch key, separately from the export's exact join. New batches stay unlabelled. Movement measures **all saved stock**, including Good stock, Not actionable, Incorrect Stock, blank and unrecognised statuses, using **batch-level delta accounting** per physical batch key: carried over is `min(LM, CM)`, reductions on existing batches are `LM − carried over` (never negative; measures saved inventory change, not proven sales or disposal), and new inclusions combine new keys plus any stock added to previously present keys. Each calendar month uses its latest saved snapshot; status edits cannot change stock totals or physical batch membership. Clicking any movement cell opens the interactive multi-month waterfall bridge overlay (supporting inline BDT/KL switching, hover reconciliation cards, and `←`/`→` month stepping). CSV exports the matching Brand, Parent SKU, Town and RSL scope for all months in both BDT and KL at saved precision. On first open after this update, an older completed run refreshes its cached movement and filters from its private snapshots, retaining its frozen stock, prices, report and demand data; restart the app server and refresh the browser to load both the updated movement logic and waterfall overlay. Archiving sets aside an unsettled attempt in Archived runs; settled records remain in Settled months. All run folders stay under the same runs directory. A settled run must be reopened before deletion. Replacing or removing a shared export materializes older runs' symlinks so those dashboards remain readable. Deletion retries read-only private files; a still-open Excel file yields a close-and-retry message and the run stays listed. The server never modifies `sample_input/`.

The cycle's E2E checks use real HTTP requests, pipeline calculations, Excel downloads and temporary git repositories, followed by widescreen browser interactions and screenshots. Run `uv run python -m tests.e2e_action_plan_cycle`, then `node tests/e2e_action_plan_cycle.cjs`. The browser script needs Chromium and `playwright-core` (install with `npm install --prefix validation-artifacts/run-upload playwright-core`). Evidence is saved under `validation-artifacts/action-plan/`. Both checks exercise the cycle end to end.

The current dashboard needs saved history for movement comparisons and secondary sales ending one month before the FSNE month; unsuitable inputs fail visibly instead of falling back to sample data. Legacy sample configurations without an `fsne_month` entry retain their original same-month convention when explicitly rebuilt. The static `ui/index.html` and static preview can display results, but uploads require the live server. The batch table is the top 400 stock rows; the Excel download contains all rows. Rotation stays at 2×, rates use all supplied months through the report date, and batch-key whitespace follows the source queries.

In **brand-status** and **town-status**, **Stock category** is a checkbox multiselect alongside Brand, Parent SKU and Town. Both reports start on stock-bdt with Incorrect stock deselected; Actionable, Non actionable, Good stock and Unclassified are selected. Incorrect stock remains available in the dropdown, and Select All includes it. Legacy `Actionable - …` labels belong to Actionable; Non actionable matches the saved `Not actionable` status. Blank or unknown labels stay Unclassified. Categories combine with OR and the other filters with AND. Rows, totals, RSL amounts, stock trends, monthly charts and DOH follow the selection in both units. Town and brand retain independent selections through metric, unit, sort and hierarchy changes. Each month uses its own latest saved snapshot and status; accepted plans refresh category facts while frozen quantities and prices stay unchanged. Existing completed runs acquire category data from their own private snapshots on first load. Linked evolution and Show data retain the selected category boundary.

In **brand-status** and **town-status**, **Exposure** mode treats each cell as 100% of that cell's exposure, including **>10M**. Exposure uses the same light red as Stock mode's Exposure tail. The darker red segment has width = L&D FC ÷ that cell's Exposure. Its tooltip labels this denominator explicitly. **<3M** cells remain unshaded in every mode. Data labels are aligned inside the left edge of each status-report cell. **L&D FC** mode shows plain values without colour coding. **Stock** mode retains its RSL colours: each nonzero **>10M** bar spans the full cell and represents that cell's stock, with a light red Exposure tail and red L&D FC tip, each divided by that cell's stock. Hover an RSL cell for all three values. Displayed values follow the selected metric and units.

In **rescue**, **rebalance** and **loading-watch**, right-click an **Exposure** or **LND FC** cell and choose **Show evolution**. The chart carries the row scope, units, report date and the report’s RSL group (3M–6M, 6M–10M or >10M). The same action works on Exposure/LND RSL cells and totals in **brand-status** and **town-status**. Within the Sankey, right-click a category or band and choose **Show RSL evolution**, or right-click an RSL group or band and choose **Show category evolution**. You can drill further within either selected view; **Back** restores the preceding graph, and Close returns to the report. Keyboard menus use **Shift+F10**. Any node or band also offers **Show data**, a full-screen brand-status view (Brand → Parent → Town by default, reorderable) limited to the rows behind that selection, at that node's snapshot month and scaled to the traced amount; see `docs/inventory-evolution.md`.

Evolution reads frozen snapshots, so its endpoint can differ from a report forecast refreshed from current rates. See [Inventory evolution](docs/inventory-evolution.md) for scopes, lenses, units, CSV export, API routes, caching and validation.

The server binds to localhost. Large HTML dashboards are gzip-compressed when the browser accepts gzip, so previews and live runs share the same delivery path without a separate preview server. For another device, use the `preview` helper's tailnet-only proxy mode. No authentication is included: keep the writable app on the trusted tailnet. Override `FSNE_RUNS_DIR` and `FSNE_HISTORY_DIR` with absolute paths when needed. A mounted URL such as `/fsne-app/` is supported.

## Sidebar

Press `[` or choose **Collapse** to shrink the sidebar to its icon rail; the width stays fixed and the choice is remembered. The collapsed rail keeps the main report shortcuts under **Reports**: **O** overview, **S** total-stock-movement, **T** town-status, **B** brand-status, a Sankey icon for inventory-evolution and **N** not-actionables. Other reports and action plans are reachable from the expanded sidebar.

## Report filters

Detailed reports use three connected value filters: **Brand**, **Parent SKU**, and **Town**. Open a dropdown, search names or parent codes, and check the values to include. **(Select All)** toggles the displayed values; during search it applies to the search results. **Add current selection to filter** combines search results with the previous selection. **OK** applies changes; **Cancel**, Escape or clicking outside discards them. An empty selection cannot be applied. Use **Clear filter from…** for one field or the × button to clear the three fields.

Values within a field combine with OR; fields combine with AND. Each dropdown offers values matching the other two fields and the report’s month, RSL or review controls. Existing selections are retained when another filter excludes them, so incompatible selections show an empty result instead of silently broadening the report. Filters persist when changing units, sorting or hierarchy and remain separate between reports. Table totals, related charts, DOH explanations, evolution scopes and connected CSV exports follow the selected facts. The batch-wise report remains a top-400 preview; its filters and filtered cards apply to that preview population. The national Overview retains its national scope. Inventory evolution uses the same dropdown interaction for its additional Material and RSL fields.

### Quick filter

Every report table with these dropdowns (total-batch-wise, transfer-candidates, sales-push, total-stock-movement, brand-status, town-status, the action-plan pages and not-actionables) also has a quick filter in its formula bar. It sets the same dropdown values, so the dropdowns, totals and exports always agree with it. **Quick filter ?** in the report header opens a guide whose examples use the open report's data.

- **Type:** press `/`, click the formula bar, or type a letter while the table has focus. `bpcno,shmp+sylhet` means (Brand containing BPCNO or SHMP) and Town Sylhet: `,` is or within one field, `+` is and, `!word` or `-word` excludes. Spaces around operators are optional. Quote a name that itself contains `+` or `,` (`"SAF ACTV+ 100G"`); picking a suggestion adds the quotes. Words match by prefix, contained text or one typo; `"Exact name"` names one value and `town:`, `brand:`, `sku:`/`parent:` or `cat:` force a field. The table previews live with a count; Enter applies, Esc restores. Tab switches the field of an ambiguous word or applies the suggested fix when the bar warns (for example two brands joined by `+`). Two fields joined by `,` cannot be expressed as dropdown values, so the bar warns instead of applying them.
- **Select:** Ctrl+click (⌘+click on Mac) cells, Shift+click a block, click a row number for the whole row, Space for the clicked cell, or Alt+click a tree row for its own level only. Then **F** keeps the selection (hold F to peek, tap F again to flip between filtered and everything), **X** excludes the clicked values, **+** adds them to the current filter. Plain click still opens each table's details.
- **Chips:** the formula bar lists each field's filter; click one to edit it, × or Backspace removes one, Shift+Backspace clears all. Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y on Windows and Linux) undo and redo any filter change in that report, including dropdown changes.

Keys act only while a report table (or, for `/`, `?` and undo, the report page) has focus and never inside text boxes. None of them is a Chrome shortcut; Ctrl+K/E/P/F/L, Ctrl+1–9, Shift+Esc, Alt+letters and F-keys stay with the browser. The parser and selection rules live in `ui/quick_filter.js` and are tested by `tests/quick_filter.test.cjs`. A new sheet joins with one `FSNEQuick.attach('#table', {group, sheetState, path, colDim?, tree?, noun})` call next to its filter group.

Town-level stock movement and action-plan cohort projections are derived from saved snapshots. Opening an older completed run adds these derived fields without changing its frozen report calculations or prices. After updating, restart the app server and refresh the browser to load both the filter data support and the new controls.

## Not-actionables

**Reports → not-actionables** lists stock explicitly marked `Not actionable`, across all RSL bands, with Stock, Batches, RSL and Action-plan remarks in its expandable table. Expand Brand → Parent SKU → Town → Batch, select Brand, Parent SKU and Town in the searchable multiselect dropdowns, filter by RSL, switch BDT/KL or export matching batches to CSV. Accepted action plans refresh the population; earlier months retain their own values and prices. Existing completed runs load the report from their private snapshots on first visit. See [Not-actionables](docs/not-actionables.md) for the selection rule and lifecycle behavior.

## National overview

**Reports → Overview** is the national stock view. It shows no brand or town breakdowns. The selected saved month controls the stock total, RSL table, four stock-trend cards and the lower Exposure/LND FC comparisons. Choose a month in the header or on the national stock chart; the header also supports left/right arrow keys. BDT/KL changes the displayed amounts and all ratios are calculated from those amounts.

- The hero table has one row per RSL group and a Total row. Stock percentages use national stock; Exposure and LND FC percentages use that row’s own stock.
- Each RSL card shows stock, its share of national stock and its change from the previous saved month. Trend lines use a separate scale per card, with subtle month labels and peak/trough values.
- The bottom cards show Exposure and LND FC by RSL, the LND FC change from the previous saved month, and a comparison with that month. Exposure and LND FC amounts and percentages match the hero table: both percentages use the RSL group’s Stock. Each risk bar represents 100% of that group’s Stock, with a green base, pink Exposure tail and dark red LND FC tip, following the status reports’ >10M color logic. The first saved month shows LND ÷ Exp instead of a change.
- Two small trend charts sit beside the comparison card, stacked: **Exposure trend** (total Exposure, with the > 10M group's Exposure as a second line on the same scale) and **LND FC trend**. Both mark the selected month and label the peak and trough.
- Stock changes are neutral. Falling Exposure/LND FC is green and rising is red. The LND ÷ Stock change is shown in percentage points.
- The layout fits widescreen laptop viewports without scrolling, in light and dark themes.

Saved months retain their own prices. Stock, Exposure and LND FC in a historical row must use the same batch price. Newly calculated reports use the uploaded shelf-life file’s `DB Landing value`; earlier settled snapshots and completed runs are not repriced when a new month is calculated. See [stock valuation](docs/stock-valuation.md).

## Sample data

The sample files and reference workbook are available separately as [fsne-sample-data-v1.zip](https://github.com/1am2syman/fsne-tool/releases/download/sample-data-v1/fsne-sample-data-v1.zip). Download and extract the ZIP into the repository root so that `sample_input/` and `sample_output/` sit alongside `README.md`. These directories are ignored by Git. The archive's SHA-256 is `688a8acdf6fb126184cdebef60a68ea67a3f28e272dfdbe2f1db81dfd90977f9`.

The app's upload workflow does not require these samples. The sample report, static dashboard build, full sample comparison and tests that use the real sample do require them.

## Generate the inferred final sample report

```bash
uv sync
uv run python -m src report --data-dir sample_input --output output/inferred_report.xlsx
# Verify raw rates against the reference, reporting valuation and queue differences:
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
```

`src/report.py` infers the missing final calculation layer from the supplied sample, without reading reference data during generation. Its raw-rate comparison checks all 27,891 rows and 30 columns; valuation and the approved queue correction differ explicitly from the reference. Exposure and LND FC now use the same 90-day selling queue, so Exposure cannot be below LND FC. Rotation req uses that queue at 2× rate. Saved reports retain their original figures, so comparisons across the first corrected month include a method change. Production rates also apply seasonality-aware upper-tail winsorization, so rate-dependent outputs intentionally differ from the original reference. See [docs/inference.md](docs/inference.md) for original formulas and [docs/run-rates.md](docs/run-rates.md) for the adjustment policy, audits and DOH column.

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

- The translated query modules use native, chained Polars lazy expressions for filters, casts, text operations, aggregation, deduplication and joins. They avoid pandas, Python row loops, `map_elements`, `map_rows`, and row-wise UDFs; Python iteration handles filesystem/workbook metadata, column expressions and the single header row. Other application modules may iterate over aggregated results when assembling views.
- Run rates clamp negative sales **before** monthly aggregation, then conservatively winsorize isolated positive spikes after a leave-one-town-out product/month seasonal adjustment. Sparse histories and ambiguous peaks are preserved. The adjusted sum is divided by the **global** distinct month count; missing pair-months count as zero. Raw `sec_history` is unchanged. See [the complete policy](docs/run-rates.md).
- Headers are promoted once after source concatenation, matching the M scripts. Only `history` explicitly removes repeated header rows. Other sources containing repeated headers can therefore raise conversion errors, just as the original queries do; they are not silently cleaned.
- `ph` fills missing parents with material codes and keeps the first duplicate material. Files use sorted recursive paths for deterministic selection; M's unspecified folder/deduplication ordering may choose a different duplicate.
- `soh_db` uses left joins (including null-key matching). Its batch key lowercases town names without replacing spaces. `sec_history` additionally replaces spaces with underscores. These intentionally different source rules are preserved.
- Stock filtering preserves the original pre-cast literal comparison: CSV `"0"` is removed, but `"0.0"` is retained. The subsequent M comparison of a text cell to numeric `0` does not remove it.
- `history.batch_index_short` removes the two rightmost hyphen-delimited suffixes. `Expiry Date` remains an integer in `history`, but is a date in `soh_db`.
- Numeric conversions are strict; Int64 conversions use half-to-even rounding. Nulls are retained. Dates accept ISO dates/timestamps, US month/day dates, `DD-Mon-YYYY` / `DD-Mon-YY`, `Mon-YYYY` (first of month), and Excel serial days (1899-12-30 epoch). Invalid non-null dates raise errors. The M scripts omit culture; ambiguous dates use **en-US**, which may differ from your Excel locale.

## Excel boundary and limitations

Polars' Calamine reader handles worksheet/table data. `openpyxl` is used only to discover named tables in modern workbooks, not to transform rows. Legacy `.xls` / `.xlsb` worksheets are supported by Calamine; `config_table` discovery requires `.xlsx` / `.xlsm`. Cached formula values must be available: Python does not recalculate Excel formulas.

Exotic named ranges, Excel-specific error cells, quoted hyphens inside history batch identifiers, and 1904-date-system numeric serials are not emulated. Inputs containing these need workbook-specific validation. The date epoch and locale can be adjusted in `src/io.py`.

The optional sample download supplies the inputs and final reference report in `sample_input/` and `sample_output/`. Tests cover synthetic boundaries and generated Excel/CSV integration; `src.compare_report` checks the complete real sample and Excel serialization. No independent second-period reference or original final formulas are available.

## Verify

The type checker uses the project's `.venv`. After `uv sync`, run `uv pip install pytest` to make the test imports available there; `uv run --with pytest` provides pytest only for that command.

```bash
uv run python -m unittest discover -s tests -v
uv run --with pytest python -m pytest -q tests/test_fsne_month.py tests/test_form_config.py tests/test_upload_samples.py tests/test_onboarding_must_have.py tests/test_server.py tests/test_run_rates.py
uv run python -m unittest tests.test_saved_report_cache tests.test_history_parquet -v
uv run python -m unittest tests.test_action_plans -v
POLARS_MAX_THREADS=2 uv run python -m pytest tests/test_filter_data.py tests/test_movement.py tests/test_server.py -q
node --unhandled-rejections=strict --test tests/overview.test.cjs tests/report_filters.test.cjs tests/quick_filter.test.cjs tests/doh_summary.test.cjs tests/evolution_page.test.cjs tests/evolution_layout.test.cjs
uvx ruff check src tests ui server
uvx pyright --project pyrightconfig.json
```

The focused pytest checks cover FSNE-month detection, previous-month sales validation, month-end and year rollover, upload persistence and calculation integration. The history tests cover settlement and reopening, first-use Parquet repair, full-record caching after projected reads, reuse across runs, concurrent readers, preservation of supplied Parquets, and failed-write cleanup. Filter regressions cover connected catalogs, Excel selection/search behavior, frozen movement reconciliation in both units, completed-run upgrades and filtered DOH/evolution scopes. Overview regressions verify that the lower Exposure/LND FC percentages match the hero table in BDT and KL, that bar widths use each group’s Stock, and that zero-stock rows show unavailable percentages. UI validation uses light-mode screenshots at widescreen laptop widths; use `validation-artifacts/` for temporary screenshots and browser evidence, then remove task-owned artifacts before delivery. The `tests/e2e_*` suites are separate and are not required for UI-only work.

## Rebuild generated outputs and the UI

For a template-only change (`ui/template.html`, `ui/onboarding.js`, `ui/doh_overlay.js`, `ui/report_filters.js`, `ui/quick_filter.js`), a completed live run picks up the current template on reload and keeps its frozen data. For the bundled sample, re-render an existing built page without recalculating the pipeline:

```bash
uv run python -m ui.rerender ui/index.html
```

`ui/index.html` must already contain sample results; `--html PATH` selects another output file. This command intentionally rejects a completed run’s page, which must retain its run identity through the live server. If the sample page is absent or report data changed, use the full build below. On a memory-constrained server, set `POLARS_MAX_THREADS=2` for the build and run it in the background.

Only the final report files in `output/` (`inferred_report.*`, `comparison.*`, `current_measures.json`) and the sources are tracked.
Everything else is regenerable and gitignored: the other `output/*` files, `output/legacy-compare/`, `ui/index.html` and `validation-artifacts/`.

```bash
# 1. optional movement exports from the saved reports in history/
uv run python -m src movement_series  --history-dir history --output output/movement_series.parquet
uv run python -m src movement_parents --history-dir history --output output/movement_parents.parquet
# 2. adjusted report + run-rate/transfer/sales-push outputs + ui/index.html
uv run python ui/build.py
# 3. serve the dashboard and its run API together
uv run python -m server --port 8770
# On a preview host, publish this localhost server with the preview helper:
# preview --proxy 8770 fsne-ui
# An optional static copy can display saved reports, but cannot run the upload API.
# cp ui/index.html ~/previews/fsne-ui/index.html
# optional: parity of the original unadjusted formulas (separate output dir)
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
```

`ui/build.py` also writes `run_rate_audit.parquet`, `run_rate_flags.csv`, `run_rate_impact.json`, `run_rates.parquet`,
`legacy_report.parquet`, `transfer_comparison.parquet` and `current_measures.json`. See `docs/run-rates.md`.

## total-stock-movement (LM vs CM, by brand)

```bash
uv run python -m src movement --history-dir history --output output/movement.parquet
uv run python -m src movement_series --history-dir history --output output/movement_series.parquet   # last 6 months, every brand
uv run python ui/build.py   # embeds the series in ui/index.html (brand x month heatmap)
```

`src/movement.py` uses columnar Polars joins and aggregations over **all saved stock**. The latest snapshot in each calendar month is selected before comparing the last two months (`movement`) or the last six comparisons (`movement_series`). Planning labels do not filter the calendar or stock population. A single snapshot has closing stock but no available opening balance or reduction percentage.

A CM batch is a *new inclusion* when its key (`batch_index` minus its last two hyphen segments) is absent from **all LM stock**. `Net reduction on existing batches = LM stock − CM stock on keys present in LM`; `Net reduction % = Net reduction / LM stock`. Negative reduction means existing keys grew. `Net change % = (CM closing stock − LM stock) / LM stock`, including new keys. These are inventory differences, not proven sales or disposal. RSL comparisons assign matched opening stock to the key's closing RSL group, so the groups add up to the full movement.

The dashboard derives its BDT and KL series directly from private saved snapshots through `ui/movement_data.py`; the optional Parquet movement exports are not build prerequisites. Values retain saved precision and snapshot prices. Filters and CSV use the same facts, with CSV including both units for every month, brand, parent and total in the selected scope.

Regression checks cover status-only edits, monthly snapshot selection, units, filters and cached upgrades of provisional, accepted and settled runs. To run the Python suite, install pytest into the development environment with `uv pip install pytest`, then run:

```bash
POLARS_MAX_THREADS=2 uv run python -m pytest -q tests
node --test tests/*.test.cjs
```

These commands exclude the separately invoked `tests/e2e_*` scripts. For status UI changes, also validate a completed run in light mode at a widescreen laptop size. Keep temporary runs, exports, logs and screenshots in a task-owned temporary directory and remove them after validation; retain the maintained regression tests.
