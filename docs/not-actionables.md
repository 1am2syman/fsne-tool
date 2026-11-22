# Not-actionables

**Reports → not-actionables** shows batches explicitly marked `Not actionable` in the selected month's snapshot. Status matching ignores surrounding whitespace and letter case. Every RSL band is eligible. Remarks and shelf life do not assign a status; `Incorrect Stock`, `RSL>10M_Good stock`, blank and other statuses stay outside this report.

The spreadsheet shows Stock, Batches, RSL and Action-plan remarks, with a compact toolbar above it. The spreadsheet expands **Brand → Parent SKU → Town → Batch**, with actual SKU descriptions and codes at batch level, RSL and saved action-plan remarks. Select a cell to read its complete text in the formula bar. Table totals and CSV follow search and RSL filters. The BDT/KL switch changes stock amounts and their sort order together. BDT is displayed in millions; CSV contains raw BDT and KL values.

Each calendar month uses its latest complete saved snapshot, including months with no matching batches. Stock retains that snapshot's quantities and prices. Parent descriptions come from all stock in the same snapshot, preferring the parent's own SKU description.

In a completed run, the current month's status comes from the private snapshot: carried statuses before a returned plan is accepted, uploaded statuses after acceptance, and settled statuses in Final view. The existing lifecycle flag identifies the current run's state. Historical selections use their own saved statuses. Plan notes do not change this population until their workbook has been uploaded and accepted.

The report uses every batch, independently of the 400-row batch preview. `src/not_actionables.py` normalizes snapshots and selects the cohort; `ui/not_actionables_data.py` serializes monthly rows. `ui/build.py` regenerates this payload during both normal builds and frozen action-plan rebuilds. Existing completed runs acquire the report on first load from their private history, preferring Parquet to Excel, without recalculating their frozen results. Standalone pages need a data rebuild to gain the new payload.

After updating, restart the app server and refresh the browser. Completed runs load the report from their saved snapshots on first visit; rebuilding those calculations is unnecessary. Rebuild standalone sample pages with `POLARS_MAX_THREADS=2 uv run python ui/build.py` to add the report data.

Verification: `POLARS_MAX_THREADS=2 uv run python -m unittest tests.test_not_actionables -v`. The suite covers status boundaries, all RSL bands, legacy columns, empty latest cohorts, duplicate keys, full populations, historical values, existing-run loading and accepted-plan refresh. UI validation uses light-mode screenshots at widescreen laptop widths.
