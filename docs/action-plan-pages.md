# Action plan pages: rescue, rebalance, loading-watch

Sidebar: **Action plans → rescue · 3M–6M**, **rebalance · 6M–10M**, **loading-watch · >10M**. One implementation, parameterised by band: `src/rescue.py` (transforms and the `Page` definitions), `ui/rescue_data.py` (payload per page), one view in `ui/template.html`, `server/plan_notes.py` (plan notes). Built by `ui/build.py` for the sample preview and for every run. The pages differ only as listed under [Band differences](#band-differences); everything else below holds for all three.

## What it shows

Only established report measures; nothing is re-forecast. Every page has the summary strip, the Exposure split bar, the spreadsheet-style sheet (drill-down hierarchies, search, Expand all, BDT/KL switch, Export CSV), the Coverage and Action plans columns, **New plans**, and the two outcome panels.

| Element | Definition |
| --- | --- |
| Exposure | `exposure_*` of the page's current batches. Up to 10M (≤ 300 days RSL) it equals stock: the batch is already past the 300-day line. Above 10M it is only the part of stock not sold before 300 days RSL. Red fill from the left = LND FC share of it, like every fill in the app. |
| LND FC | `lnd_fc_*`: stock still unsold at 90 days RSL at today's run rate. |
| Cases (CS) | Sum of the shelf-life file's `CS` for the row's batches (batch identity as in `soh_db`). Not unit-dependent. |
| Towns | Distinct towns holding the row's stock in the page's band: how scattered it is. |
| Coverage | Share of the row's Exposure (value or volume, following the BDT/KL switch) whose batch had a last-month action plan. The green fill spans that share of the whole cell. Hover shows the split by last month's mitigation status. `✎ planned/uncovered` counts batches without a last-month plan that a new plan reaches. |
| Action plans | Distinct last-month remarks, largest Exposure first. Full text in the formula bar. |
| LND in lots ≥ 20K BDT | LND pooled by SKU × region (region from the shelf-life file). A lot is the unit one liquidation or transfer can collect. |
| LND entering the next band in 30 days | LND of batches whose RSL falls into the next shorter band within 30 days. The threshold is per page (below). |

**Last month** is the latest saved report in the previous calendar month, joined by exact `batch_index`: the same join that fills the `lm_` columns of the action plan input file. Batches absent from it are new and count as uncovered. A remark of `RSL>10M_Good stock` is a label, not a plan.

The sheet ends at the viewport bottom, so its sticky total row is visible on load; the outcome panels follow below.

## Band differences

| | rescue | rebalance | loading-watch |
| --- | --- | --- | --- |
| Band (`rsl_group`) | 3M–6M · 91–180 days RSL | 6M–10M · 181–300 days | >10M · over 300 days |
| Rows | all batches of the band | all batches of the band | only batches with `exposure > 0` |
| Exposure | equals stock | equals stock (≤ 10M: the whole batch) | part of stock not sold before 300 days RSL |
| Extra column | none | none | **Rotation req** after LND FC |
| LND entering the next band in 30 days | RSL ≤ 120 → <3M | RSL ≤ 210 → 3M–6M | RSL ≤ 330 → 6M–10M |
| Incumbent panel | 3M–6M | 6M–10M | >10M |
| Remark panel bands | 3M–10M | 6M–10M | >10M |
| Uncovered-batch list | `output/rescue_batches.parquet` | `output/rebalance_batches.parquet` | `output/loading_watch_batches.parquet` |

**loading-watch rows.** At >10M most stock sells before the 300-day line and is not at risk, so a batch is listed only when the report's `exposure_*` is above zero. A batch with LND FC > 0 also has Exposure > 0: the corrected report uses the same 90-day selling queue for both measures. Earlier reports used separate queue rules that could violate this relationship; newly calculated reports correct it. Saved months retain their stored Exposure and Rotation req, so the first month across this change compares methods once. The saved reports behind the two outcome panels carry no exposure column, so those panels cover every >10M batch with LND FC > 0.

**Rotation req.** The report's own `rotation_req_*`: the exposure still left if the run rate doubled. Exposure − Rotation req is what doubling the rate clears. It follows the BDT/KL switch and has its own total. It is not part of the CSV export, whose columns are the same on every page.

**Cases (CS)** on loading-watch are the cases of the whole batch, not only of its exposed part.

`RSL>10M_Good stock` stays a label, not a plan, on every page; it matters most on loading-watch. The lot threshold (≥ 20K BDT by SKU × region) is the same on every page.

## Inventory evolution drill-down

Right-click an **Exposure** or **LND FC** value, including an expanded parent/town row or total, and choose **Show evolution**. The overlay keeps the measure, BDT/KL display, report date and row dimensions, with 3M–6M for rescue, 6M–10M for rebalance or >10M for loading-watch. Select a cell and press **Shift+F10** to open the keyboard menu.

Within the Sankey, right-click a category or flow band and choose **Show RSL evolution**. It follows the selected quantity across RSL groups while excluding later growth. **Back to categories** restores the chart and selection; Close restores the report’s expanded rows and scroll.

Evolution uses saved snapshot figures. A current report forecast refreshed from current rates can differ from the chart’s endpoint, particularly for loading-watch Exposure. See [Inventory evolution](inventory-evolution.md) for attribution rules, units, export and API details.

## New plans for uncovered batches

**New plans** in the toolbar, or a row's `✎ planned/uncovered` badge, opens the page's batches without a last-month plan as **Brand → SKU → Town**, collapsed to brands, largest LND FC first. A plan can be set on any level:

- **Action plan**: one or more categories from a fixed list (`src/rescue.py: PLAN_CATEGORIES`), grounded in the 85 distinct remarks of the saved reports, each with the status sales gave such remarks: Sales push, Seasonal sales push, Trade offer (TLO / QDD / 2+1), EC drive / activation, Free with bundle, Stock rotation, Liquidation - E-Com / Modern Trade / RTM, Emerging channel, Office sales (Actionable); Stock adjustment (Incorrect Stock); L&D claim (Not actionable).
- **Remarks**: free text, up to 300 characters.
- **Status** is derived: the most actionable status among the chosen categories (Actionable, then Incorrect Stock, then Not actionable).

The nearest plan wins for each batch: town over SKU over brand. A lower plan replaces a wider one; it does not merge. Plans belong to a page: a brand plan on rescue never applies to that brand's 6M–10M or >10M batches, and each page sets its own. Rows without their own plan show the inherited one (`↳ …`, "from Brand"/"from SKU"); `×` removes a row's own plan. Every change saves at once (`POST api/runs/<id>/notes` with `page` = `rescue` | `rebalance` | `loading-watch`, stored with who and when in the run's `plan_notes.json`, each plan keyed by page, scope and names). `GET api/runs/<id>/notes` returns the plans of all pages, each carrying its `page`. A `plan_notes.json` or a POST without a page means rescue, so notes written before the other pages existed keep working.

Plans reach only the uncovered batches listed in their page's batch file in the run's `output/` (written with the dashboard data; table above). They:

- pre-fill `action_plan` (categories then remarks, joined by `; `, as sales write them) and `mitigation_status` of the run's **Action plan input file**, from all three pages together (batches are unique across bands); the file is rebuilt on the next download when plans changed;
- never reach the snapshot or history directly: only the uploaded and accepted plan file does, through the existing check and settle cycle;
- are read-only once the month is settled, and on saved static pages;
- appear in the CSV export (`new_action_plan`, `new_mitigation_status`, `new_plan_scope`).

## Outcome panels (saved reports only)

Latest saved report per calendar month. The cohort is last month's batches of the page's band(s) with LND FC > 0, matched to this month by the movement batch key (`batch_index` without its last two segments), so new inclusions never count as reductions.

- **LND FC reduced from last month · incumbents** (the page's band): `(LM LND − same batches' LND now) ÷ LM LND`, per month. At forecast pace this stays near zero; only beating the forecast raises it.
- **Which remark moved LND** (bands per page, 3M–10M on rescue): the same, pooled over the latest three months and grouped by last month's remark keyword. First match wins: Incorrect Stock status, `l&d`, `liquidat`, `rotation`, `tlo|qdd|2+1|incentive`, `winter|season`, `push`. Association, not proof of cause.

BDT values in these panels are the saved values of each month; months valued before the shelf-life `DB Landing value` switch compare different bases once (see [stock-valuation.md](stock-valuation.md)). KL is basis-free.

Runs calculated before this report existed show a notice; a new run builds it.
