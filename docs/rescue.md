# rescue (Action plans · 3M–6M)

Sidebar: **Action plans → rescue**. Code: `src/rescue.py` (transforms), `ui/rescue_data.py` (payload), view `rescue` in `ui/template.html`. Built by `ui/build.py` for the sample preview and for every run.

## What it shows

Only established report measures; nothing is re-forecast.

| Element | Definition |
| --- | --- |
| Exposure | `exposure_*` of current 3M–6M batches (91–180 days RSL). At this age it equals stock: the batch is already past the 300-day line. Red fill from the left = LND FC share of it, like every fill in the app. |
| LND FC | `lnd_fc_*`: stock still unsold at 90 days RSL at today's run rate. |
| Cases (CS) | Sum of the shelf-life file's `CS` for the row's batches (batch identity as in `soh_db`). Not unit-dependent. |
| Towns | Distinct towns holding the row's 3M–6M stock: how scattered it is. |
| Coverage | Share of the row's Exposure (value or volume, following the BDT/KL switch) whose batch had a last-month action plan. The green fill spans that share of the whole cell. Hover shows the split by last month's mitigation status. `✎ planned/uncovered` counts batches without a last-month plan that a new plan reaches. |
| Action plans | Distinct last-month remarks, largest Exposure first. Full text in the formula bar. |
| LND in lots ≥ 20K BDT | LND pooled by SKU × region (region from the shelf-life file). A lot is the unit one liquidation or transfer can collect. |
| LND entering <3M in 30 days | LND of batches with RSL ≤ 120 days. |

**Last month** is the latest saved report in the previous calendar month, joined by exact `batch_index`: the same join that fills the `lm_` columns of the action plan input file. Batches absent from it are new and count as uncovered. A remark of `RSL>10M_Good stock` is a label, not a plan.

The sheet ends at the viewport bottom, so its sticky total row is visible on load; the outcome panels follow below.

## New plans for uncovered batches

**New plans** in the toolbar, or a row's `✎ planned/uncovered` badge, opens the batches without a last-month plan as **Brand → SKU → Town**, collapsed to brands, largest LND FC first. A plan can be set on any level:

- **Action plan**: one or more categories from a fixed list (`src/rescue.py: PLAN_CATEGORIES`), grounded in the 85 distinct remarks of the saved reports, each with the status sales gave such remarks: Sales push, Seasonal sales push, Trade offer (TLO / QDD / 2+1), EC drive / activation, Free with bundle, Stock rotation, Liquidation - E-Com / Modern Trade / RTM, Emerging channel, Office sales (Actionable); Stock adjustment (Incorrect Stock); L&D claim (Not actionable).
- **Remarks**: free text, up to 300 characters.
- **Status** is derived: the most actionable status among the chosen categories (Actionable, then Incorrect Stock, then Not actionable).

The nearest plan wins for each batch: town over SKU over brand. A lower plan replaces a wider one; it does not merge. Rows without their own plan show the inherited one (`↳ …`, "from Brand"/"from SKU"); `×` removes a row's own plan. Every change saves at once (`POST api/runs/<id>/notes`, stored with who and when in the run's `plan_notes.json`).

Plans reach only the uncovered batches listed in the run's `output/rescue_batches.parquet` (written with the dashboard data). They:

- pre-fill `action_plan` (categories then remarks, joined by `; `, as sales write them) and `mitigation_status` of the run's **Action plan input file**; the file is rebuilt on the next download when plans changed;
- never reach the snapshot or history directly: only the uploaded and accepted plan file does, through the existing check and settle cycle;
- are read-only once the month is settled, and on saved static pages;
- appear in the CSV export (`new_action_plan`, `new_mitigation_status`, `new_plan_scope`).

## Outcome panels (saved reports only)

Latest saved report per calendar month. The cohort is last month's batches with LND FC > 0, matched to this month by the movement batch key (`batch_index` without its last two segments), so new inclusions never count as reductions.

- **LND FC reduced from last month · 3M–6M incumbents**: `(LM LND − same batches' LND now) ÷ LM LND`, per month. At forecast pace this stays near zero; only beating the forecast raises it.
- **Which remark moved LND · 3M–10M**: the same, pooled over the latest three months and grouped by last month's remark keyword. First match wins: Incorrect Stock status, `l&d`, `liquidat`, `rotation`, `tlo|qdd|2+1|incentive`, `winter|season`, `push`. Association, not proof of cause.

BDT values in these panels are the saved values of each month; months valued before the shelf-life `DB Landing value` switch compare different bases once (see [stock-valuation.md](stock-valuation.md)). KL is basis-free.

Runs calculated before this report existed show a notice; a new run builds it.
