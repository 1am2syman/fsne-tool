# Sales-push feasibility (Phase A, issue #2)

Answers: *what selling pace would clearing this stock by a chosen date need, and what happens
under a few explicit uplift assumptions?* Code: `src/sales_push_feasibility.py` (reuses the
shared queue `inventory_projection.deplete`). Legacy warning-based exposure in `report.py` is
untouched.

## Meaning and conventions (v1)

- 300/90-day warnings are **internal thresholds, not commercial cutoffs**; they are never used as
  a deadline. The deadline is an explicit `deadline_kind`: `planning_target` (default, a
  user-selected target, 90 days), `commercial_acceptance` or `actual_expiry`. Real acceptance
  rules do not exist as inputs yet (open owner question).
- `deadline_days` = calendar days after the snapshot; sales run on days 0..deadline-1. Batches
  also stop selling on their own expiry day.
- Pool = town + child SKU; the town-parent rate is allocated once by child sales share (same as
  the risk-calendar projection). Stock ahead in the queue consumes the same demand. Rates are
  historical sales, **not** unconstrained demand.
- Uplift multiplies the baseline only for `delay <= day < delay + duration`. It is an
  **assumption**, never a likelihood of success. No causality or seasonality is claimed, and
  cannibalisation/substitution is not modelled.
- Required uplift is found by bisection over `[1, 20]`, tolerance 1e-4, residual tolerance
  1e-6 KL. Residual stock is non-increasing in the multiplier, so bisection is valid; results are
  labelled `solved_approximate`. Beyond the bound: `not_feasible_within_bounds`.

## Outputs

```bash
uv run python -m src sales_push_requirements --data-dir sample_input --output output/sales_push_requirements.parquet  # pool
uv run python -m src sales_push_feasibility  --data-dir sample_input --output output/sales_push_feasibility.parquet   # pool x scenario
```

Requirements per pool: `simple_required_kl_week` (= (target + stock ahead) / weeks, explanatory
isolated arithmetic only), `required_total_kl_week` (pool sales/week at the minimal solving
multiplier), `required_incremental_kl_week` (minus baseline sales), `required_uplift`,
`solver_status`, `review_state` (`supported_assumptions`, `stretch_scenario`,
`incomplete_evidence`, `infeasible_under_stated_constraints`), `reason_codes`.
Unknown baseline gives no numbers; a zero baseline gives no ratio (`zero_baseline`); deadline
at/before the snapshot with stock is infeasible, never a divide-by-zero.

Scenarios: `no_action`, `uplift_1_5x`, `uplift_2x`, `uplift_2x_delay_14d`,
`uplift_2x_first_30d` with projected sales/residual KL and reason codes
(`residual_remains`, `expiry_before_deadline`, ...). Both frames carry `rate_basis`,
`policy_version` and every scenario parameter.

Hand-checked fixture (in tests): 12 KL, 28 days, 2 KL/week → required 3 KL/week total,
1 KL/week incremental, 1.5×, baseline residual 4 KL.

## Sample run (30 Aug 2026 snapshot, 22,722 pools, ~13 s)

7,762 clear at baseline; 7,388 need an uplift ≤ 20×; 858 are not feasible within bounds;
1,134 have an unknown and 5,580 a zero baseline (incomplete evidence). Recompute, don't quote.

## Historical plausibility (step 5)

`history_evidence` reads the audited monthly series from `run_rate_parent.adjustments` (source
months up to the snapshot only, negatives clamped, no future data) and adds to each
requirements row: `hist_window_months`, `hist_observed_months`, `hist_calendar_gap_months`
(window months absent from every town, e.g. the 2 missing in the sample), observed peak
KL/week and month, last-3-month pace (absent months count as zero; months observed are shown),
winsorized / ambiguous-peak month counts, and `hist_comparison`:
`within_observed_range`, `above_observed_peak` (adds reason `required_above_observed_peak`),
`no_history` or `not_applicable`. Evidence is town + parent, scaled to the child by its window
share (the same split as the rate).

Being above the peak is a review flag, not impossibility; being within it does not show the
pace is achievable. Stock availability is not supplied, so stockout-censored months are not
identified; one annual cycle cannot show seasonality or promotion effect.

Sample (22,722 pools): 12,488 within the observed range, 8,242 above the observed peak,
858 not applicable (infeasible), 1,134 no history.

## UI (`sales-push` report)

Precomputed by `ui/sales_push_data.py` (the page has no formulas of its own, so it matches
Python exactly). Declared scenarios only, one planning-target deadline (90 days). Search,
scenario and review-state filters, an explanation panel per pool, and a CSV export of every
filtered pool (44 columns: inputs, outputs, history, scenarios, caveats, policy version).

## Not yet done (remaining issue scope)

Phase B economics (needs approved price/margin/cost schemas; unavailable, not zero), Phase C
outcome evaluation (depends on #5), a user-selectable deadline (needs a compute service, not a
static page), automated browser tests, and commercial/finance review of the fixtures.
