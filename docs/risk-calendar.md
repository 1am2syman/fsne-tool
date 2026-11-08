# Upcoming-risk calendar (Phase A)

Answers: *which stock reaches a warning date or expiry in the next 30/60/90 days?*
Implemented in `src/risk_calendar.py`; UI payload in `ui/risk_calendar_data.py`;
view `risk-calendar` in `ui/template.html`.

## Semantics (confirmed / frozen for v1)

- 300 and 90 days remaining shelf life are **internal warning thresholds, not commercial
  selling cutoffs**. Crossing one does not imply unsellable stock, loss, write-off or
  mandatory discounting. Warnings, actual expiry, channel acceptance deadlines and
  latest-action dates are distinct concepts; only warnings and expiry exist in Phase A.
- 180 days is shown as a display event because it is an existing RSL band boundary.
  **Confirmed** by the business owner (2026-10-14) as a display event.
- `event_date = expiry_date - threshold_days` (actual expiry: threshold null).
  `days_until_event = event_date - snapshot_date` in calendar days. Day 0 is due today.
  Past events are kept (overdue), never dropped.
- Bands (mutually exclusive): due/overdue (<= 0), 1–30, 31–60, 61–90, later.
- Snapshot date is the report's embedded `report_date`, never the browser clock.
- Values are **current stock associated with the warning**, not forecast risk.

## Invariants

- Population: all positive-stock batches, every RSL group and mitigation status; no top-N truncation.
- One row per batch/event. Bands are additive within one event type; different event types
  describe the same stock and are **non-additive**.
- Known BDT totals exclude unpriced stock; excluded KL and batch counts are reported. A null
  value is never zero. Missing demand rate is flagged (`demand_known = False`), a known zero
  rate is not.
- Existing exposure is reported separately and overlaps stock; it is never summed with it.
- Inputs are validated: single snapshot, unique `batch_index`, positive finite stock, expiry
  present, `rsl_days` consistent with dates.

## Reproduce

```bash
uv run python -m src risk_calendar --data-dir sample_input --output output/risk_calendar.parquet          # grain: batch x event
uv run python -m src risk_calendar_summary --data-dir sample_input --output output/risk_calendar_summary.csv  # coverage per event x band
uv run python ui/build.py && cp ui/index.html ~/previews/fsne-ui/index.html
```

`.csv`, `.parquet` and `.xlsx` outputs are supported. In the UI, **Export CSV** on the
risk-calendar report downloads every row matching the filters (all pages, both units).

## Sample walkthrough (30 Aug 2026 snapshot)

Select *300-day warning* → *Days 1–30*: 723 batches, ≈ 7.53M BDT of current stock crosses
the 300-day warning within 30 days. Recompute rather than rely on these figures.

## Phase B: no-intervention projection (`src/inventory_projection.py`)

Approved decisions (2026-10-14) and how they are implemented:

| Topic | Decision |
|---|---|
| Allocation | Earliest expiry first within each town + child SKU pool |
| Children | **Not interchangeable**. The town-parent run rate is split **once** across children by each child's share of the parent's secondary sales in the same window (returns clamped to 0) |
| Ties | First come, first served: earliest `Primary First Date`, then batch id |
| Horizon | 90 days from the snapshot; later events show "beyond 90d" |
| Demand | Existing run-rate pipeline (`run_rate_parent`, adjusted rate, monthly rate / 30 per day); forecast origin = snapshot date; partial/future months are excluded upstream |
| Sensitivity | Baseline (1x) and 2x rate |
| Expiry boundary | Taken from the pipeline: usable days = `rsl_days`, so sales run on days 0..rsl-1 and **not on the expiry date** |
| Deadlines / lead times | None exist. Not modelled; shown as unavailable. Rotation and sales push are actions to be driven and closed (issue #5), not scheduled |
| Buffers | None |

Rules: conservation (`opening = modeled sales + remaining`, all >= 0), demand never exceeds the
allocated rate, no look-ahead, expired stock stays unsold. A missing parent rate gives an
**unknown** projection (never full consumption); a known zero rate means no modelled sales; a
child with no sales in the window gets a known zero rate (its parent's demand belongs to other
children). Events already in the past show current stock (`observed_past_event`).

```bash
uv run python -m src inventory_projection --data-dir sample_input --output output/inventory_projection.parquet  # scenario x batch x event
```

Output columns include `scenario_id`, `forecast_origin`, `demand_basis`, `allocation_policy`,
`projected_remaining_kl`, `projected_value_bdt` (remaining/stock x snapshot value, null if
unpriced), `coverage_status` and `policy_version`. The UI adds a scenario selector, a projected
remaining column and the same fields in its CSV export.

Remaining limitations: the rate is a historical average; child shares use the same window and
ignore seasonality; no receipts, returns or transfers are modelled.
