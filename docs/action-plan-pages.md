# Action plan pages: rescue, rebalance, loading-watch

Sidebar: **Action plans → rescue · 3M–6M**, **rebalance · 6M–10M**, **loading-watch · >10M**. One implementation, parameterised by band: `src/rescue.py` (transforms and the `Page` definitions), `ui/rescue_data.py` (payload per page), one view in `ui/template.html`, `server/plan_notes.py` (plan notes). Built by `ui/build.py` for the sample preview and for every run. The pages differ only as listed under [Band differences](#band-differences); everything else below holds for all three.

## What it shows

Only established report measures; nothing is re-forecast. Every page has one summary stat, **LND entering the next band in 30 days**, the spreadsheet-style sheet (drill-down hierarchies, connected Brand/Parent SKU/Town dropdowns, Expand all, BDT/KL switch, Export CSV), Coverage, current-month then last-month Action plans, and draft planning. Exposure, LND FC and cases remain in the sheet; the other summary stats and Exposure split bar are omitted.

| Element | Definition |
| --- | --- |
| Exposure | `exposure_*` of the page's current batches. Up to 10M (≤ 300 days RSL) it equals stock: the batch is already past the 300-day line. Above 10M it is only the part of stock not sold before 300 days RSL. Red fill from the left = LND FC share of it, like every fill in the app. |
| LND FC | `lnd_fc_*`: stock still unsold at 90 days RSL at today's run rate. |
| Cases (CS) | Sum of the shelf-life file's `CS` for the row's batches (batch identity as in `soh_db`). Not unit-dependent. |
| Towns | Distinct towns holding the row's stock in the page's band: how scattered it is. |
| Coverage | Share of the row's Exposure (value or volume, following the BDT/KL switch) with a plan. Once an upload is accepted, uses the current month's accepted entries immediately, including before settlement; green fill and mitigation-status tooltip use that same month. Before acceptance, shows explicitly labelled last-month coverage. Settling without an upload also retains last-month coverage. Blank current entries never fall back to last month. |
| Action plans | Two columns, current month first, then last month, each labelled with its month and year. Distinct remarks, largest Exposure first, independently aggregated for each month. Full text in the formula bar. Current month says **Awaiting accepted plan** until acceptance (**No accepted plan** if settled without upload). |
| LND entering the next band in 30 days | LND of batches whose RSL falls into the next shorter band within 30 days. The threshold is per page (below). |

**Last month** is the latest saved report in the previous calendar month, joined by exact `batch_index`: the same join that fills the `lm_` columns of the action plan input file. Batches absent from it count as last-month gaps. **Current month** comes from this run's private snapshot, joined by exact `batch_index`. Acceptance replaces its entries; loading an older completed run upgrades its cached page data from that snapshot without recalculating stock, rates or prices. A remark of `RSL>10M_Good stock` is a label, not a plan, for either month's coverage.

CSV exports include accepted current `action_plan`, `mitigation_status` and `plan_counted`, followed by the separate `lm_` fields and draft `new_` fields. Current fields stay empty/un-counted before acceptance.

The sheet ends at the viewport bottom, so its sticky total row is visible on load. These pages show the table and the single LND-entering stat; there are no outcome panels below the table.

The three dropdowns use the shared [Excel-style filter behavior](../README.md#report-filters). Each field lists values compatible with the other two. Selected facts drive the single LND-entering stat, table totals, CSV and the batches opened through **New plans**. Report filters persist through unit, hierarchy and sort changes, with independent selections on rescue, rebalance and loading-watch. Informational row/brand counts and Notes are omitted from the toolbar to keep the controls on one row.

## Band differences

| | rescue | rebalance | loading-watch |
| --- | --- | --- | --- |
| Band (`rsl_group`) | 3M–6M · 91–180 days RSL | 6M–10M · 181–300 days | >10M · over 300 days |
| Rows | all batches of the band | all batches of the band | only batches with `exposure > 0` |
| Exposure | equals stock | equals stock (≤ 10M: the whole batch) | part of stock not sold before 300 days RSL |
| Extra column | none | none | **Rotation req** after LND FC |
| LND entering the next band in 30 days | RSL ≤ 120 → <3M | RSL ≤ 210 → 3M–6M | RSL ≤ 330 → 6M–10M |
| Uncovered-batch list | `output/rescue_batches.parquet` | `output/rebalance_batches.parquet` | `output/loading_watch_batches.parquet` |

**loading-watch rows.** At >10M most stock sells before the 300-day line and is not at risk, so a batch is listed only when the report's `exposure_*` is above zero. A batch with LND FC > 0 also has Exposure > 0: the corrected report uses the same 90-day selling queue for both measures. Earlier reports used separate queue rules that could violate this relationship; newly calculated reports correct it. Saved months retain their stored Exposure and Rotation req, so the first month across this change compares methods once.

**Rotation req.** The report's own `rotation_req_*`: the exposure still left if the run rate doubled. Exposure − Rotation req is what doubling the rate clears. It follows the BDT/KL switch and has its own total. It is not part of the CSV export, whose columns are the same on every page.

**Cases (CS)** on loading-watch are the cases of the whole batch, not only of its exposed part.

`RSL>10M_Good stock` stays a label, not a plan, on every page; it matters most on loading-watch.

## Inventory evolution drill-down

Right-click an **Exposure** or **LND FC** value, including an expanded parent/town row or total, and choose **Show evolution**. The overlay keeps the measure, BDT/KL display, report date and row dimensions, with 3M–6M for rescue, 6M–10M for rebalance or >10M for loading-watch. Select a cell and press **Shift+F10** to open the keyboard menu.

Within the Sankey, right-click a category or flow band and choose **Show RSL evolution**. It follows the selected quantity across RSL groups while excluding later growth. **Back to categories** restores the chart and selection; Close restores the report’s expanded rows and scroll.

Evolution uses saved snapshot figures. A current report forecast refreshed from current rates can differ from the chart’s endpoint, particularly for loading-watch Exposure. See [Inventory evolution](inventory-evolution.md) for attribution rules, units, export and API details.

## New plans for uncovered batches

**New plans** in the toolbar, or a row's `✎ planned/uncovered` badge before acceptance, opens the page's batches without a last-month plan as **Brand → SKU → Town**, collapsed to brands, largest LND FC first. After acceptance the button reads **Draft plans**, its count is labelled **LM gaps**, and badges are removed from current Coverage so draft notes cannot be mistaken for accepted coverage. Drafts still target the same last-month gaps and only affect coverage through a subsequent accepted upload. A plan can be set on any level:

- **Action plan**: one or more categories from a fixed list (`src/rescue.py: PLAN_CATEGORIES`), grounded in the 85 distinct remarks of the saved reports, each with the status sales gave such remarks: Sales push, Seasonal sales push, Trade offer (TLO / QDD / 2+1), EC drive / activation, Free with bundle, Stock rotation, Liquidation - E-Com / Modern Trade / RTM, Emerging channel, Office sales (Actionable); Stock adjustment (Incorrect Stock); L&D claim (Not actionable).
- **Remarks**: free text, up to 300 characters.
- **Status** is derived: the most actionable status among the chosen categories (Actionable, then Incorrect Stock, then Not actionable).

The nearest plan wins for each batch: town over SKU over brand. A lower plan replaces a wider one; it does not merge. Plans belong to a page: a brand plan on rescue never applies to that brand's 6M–10M or >10M batches, and each page sets its own. Rows without their own plan show the inherited one (`↳ …`, "from Brand"/"from SKU"); `×` removes a row's own plan. Every change saves at once (`POST api/runs/<id>/notes` with `page` = `rescue` | `rebalance` | `loading-watch`, stored with who and when in the run's `plan_notes.json`, each plan keyed by page, scope and names). `GET api/runs/<id>/notes` returns the plans of all pages, each carrying its `page`. A `plan_notes.json` or a POST without a page means rescue, so notes written before the other pages existed keep working.

Plans reach only the uncovered batches listed in their page's batch file in the run's `output/` (written with the dashboard data; table above). They:

- pre-fill `action_plan` (categories then remarks, joined by `; `, as sales write them) and `mitigation_status` of the run's **Action plan input file**, from all three pages together (batches are unique across bands); the file is rebuilt on the next download when plans changed;
- never reach the snapshot or history directly: only the uploaded and accepted plan file does, through the existing check and settle cycle;
- are read-only once the month is settled, and on saved static pages;
- appear in the CSV export (`new_action_plan`, `new_mitigation_status`, `new_plan_scope`).

Runs calculated before this report existed show a notice; a new run builds it.

## Updating and checking saved runs

Restart the app server and refresh the browser after updating the backend and template. Open a completed run through the live server to load its current UI. Older cached action-plan pages gain the current-plan columns from the run's private snapshot on first load; this updates only derived dashboard data, preserving the frozen report, snapshot, rates and prices. Saved snapshot dates accept ISO dates and Excel serial days. A run without action-plan page data still needs a new calculation to create those pages.

Run the focused regressions with:

```bash
POLARS_MAX_THREADS=2 uv run python -m unittest tests.test_current_action_plans tests.test_rescue tests.test_action_plans tests.test_not_actionables tests.test_plan_notes tests.test_server
```

Validate the live UI in light mode at a widescreen laptop width. Check all three pages before and after plan acceptance in BDT and KL: current then previous-month columns, coverage and its status tooltip from the same month, blank current plans uncovered, and the single LND-entering stat above the table.
