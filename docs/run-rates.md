# Seasonality-aware run rates and DOH

## Central policy

`src/run_rate_parent.py::transform` now returns adjusted monthly secondary-sales
KL per town + parent material. All standard downstream builds consume this rate:
`run_rate_town`, `soh_db`, report DOH, consumption, rotation, exposure and liquidation.
`sec_history` remains raw sales history; its existing negative-sales clamp is
unchanged. This is an intentional business-policy change from the original M query.

Monthly source sales are first clamped nonnegative per source row and summed by
town + parent + calendar month. Batch/child rows are never individually winsorized.
The adjusted monthly sum is divided by the global distinct source-month count,
including missing pair-months as implicit zeroes. No new demand is invented for
zero or low-sales months. The supplied source currently covers 12 months.

## Seasonal protection and evidence gates

Default policy (`WinsorPolicy`) requires:

- At least 12 source months globally.
- At least 8 observed months and 6 positive months for the town-parent.
- At least 10 other eligible towns for the parent in the evaluated month.
- At least 6 positive residual observations for estimating the town's bound.

For each eligible town-parent, the baseline is its median positive monthly sales.
Normalize each observed monthly sale by that baseline. The month's seasonal factor
is the median normalized sale in **other eligible towns for the same parent**.
An exact leave-one-town-out median prevents a town from inflating its own factor;
town normalization prevents large towns from dominating the profile. Missing peer
observations are not invented, and sparse towns do not establish seasonal factors.

```text
expected_kl = local_positive_month_median × peer_season_factor
residual = raw_monthly_kl / expected_kl
center = median(positive residuals)
mad = median(abs(positive residual - center))
upper_residual = max(center + 3 × 1.4826 × mad, 2 × center)
upper_kl = expected_kl × upper_residual
```

Only the upper tail is winsorized. A month above its bound is capped only when
both adjacent calendar months are present, have positive supported residuals,
and are within the bound. Otherwise the peak is preserved and flagged. This
conservatively preserves sustained increases, boundary peaks, sparse histories,
gaps and zero-adjacent spikes. A zero MAD does not collapse the bound to the
median: the minimum bound remains twice the residual center.

A shared product-wide peak passes through the seasonal factor and is not clipped
merely for being a high-sales month. The factor captures shared monthly patterns,
which can include seasonal demand, promotions and trends; it does not prove their
cause. One annual cycle cannot establish recurring seasonality, local events may
still look like isolated anomalies, and the rate remains a cleaned historical
average—not a forward seasonal forecast. More years and event calendars would
improve identification. The policy deliberately leaves ambiguous peaks untouched.

`report.build` excludes sales months after its configured snapshot month. Monthly
inputs cannot distinguish sales before/after a date inside that month: a partial
month must be prepared appropriately upstream. Direct rate builds without an
`as_of` parameter use all supplied months, as before.

## Audits and outputs

`uv run python ui/build.py` uses the supplied `sample_input/` sources, validates
that their report date, batch identities, hierarchy and closing stock match the
latest movement snapshot, and rebuilds the current report. It fails instead of
silently combining unrelated stocks/rates. Earlier saved workbooks are immutable.
The latest status/overview quantities receive an in-memory recalculated overlay.
Every BDT measure in that overlay uses the saved snapshot’s own batch price
(`saved stock BDT / saved stock KL`), including Exposure, LND FC, Rotation req
and consumable stock. Stock and its measures must use the same valuation. Manual
mitigation statuses and stock-movement history remain saved facts.

The August 2026 snapshot was corrected once with the owner’s approval to store
these recalculated quantities at August’s own prices. It is then historical data;
later months do not reprice it. Current calculations and exports continue to use
the shelf-life file’s DB Landing value. The historical correction does not change
that pricing policy.

- `output/run_rate_audit.parquet`: every observed monthly value, expectation,
  season factor, clipping bounds, peer/history counts, reason and adjusted value.
- `output/run_rate_flags.csv`: clipped and ambiguous preserved peaks for review.
- `output/run_rates.parquet`: raw and adjusted rates per town-parent.
- `output/run_rate_impact.json`: policy, adjustment counts and old/new measures.
- `output/inferred_report.parquet` / `.xlsx`: adjusted current report.
- `output/legacy_report.parquet`: unadjusted baseline for independent reference checks.

The original reference was generated with unadjusted rates. Adjusted outputs
intentionally differ in rate-dependent columns. To verify original formula parity
without overwriting production artifacts:

```bash
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
uv run python -m unittest discover -s tests -v
uv run python validation-artifacts/verify-doh.py
node validation-artifacts/verify-doh-browser.cjs
```

The real-source verifier checks protected-file hashes, legacy reference parity,
adjusted-rate reconciliation, current build parity, Excel roundtrip and downstream
impact. It records expected policy differences in `output/comparison.json` and
`output/comparison.csv`. Do not treat the original comparator's default mismatch
as an instruction to alter the untouched reference.

## DOH column

The **town-status** and **brand-status** tables show **DOH (days)** for every
hierarchy row and the national footer. Stock movement retains its start→latest
stock column; DOH does not replace it.

```text
DOH = latest selected stock-category cohort KL with known demand
      / sum of relevant distinct town-parent adjusted monthly rates × 30
```

Each town-parent appears once in the rate payload, with stock quantities for its RSL
groups. Status DOH includes all RSL groups and follows the Stock category, Town,
Brand and Parent SKU filters. Selecting multiple categories sums their stock before
counting the town-parent rate once. With no category filter it includes all statuses.
It is independent of the displayed stock/exposure/LND metric and BDT/KL unit. Town, brand, expanded
parent and total values are independently recalculated from those
cohorts, never averaged from batch or brand DOH. Known zero rates remain zero.
A town-parent assigned to multiple brands or duplicate rate rows fails validation.

- BDT/KL switching changes displayed amounts, not DOH.
- Missing rates: exclude those town-parent pairs from both stock and demand in
  aggregate DOH. Show `⚠` alongside the value; the tooltip and explanation list
  missing-pair count and excluded KL. The explanation also lists the missing
  sources and the percentage of selected stock covered by known demand.
- No positive selected stock with known demand: `—`, including a batch with no
  matching rate source. Selected stock totals still include excluded stock.
- Covered rates sum to zero: `∞` (unbounded cover). Generated
  zero-demand batch DOH is stored as null to avoid exporting nonfinite numbers;
  `avg_sec_m = 0` distinguishes it from a missing rate, and the batch UI renders ∞.
  All consumption capacities remain zero for a known zero rate.
- Mixed known zero/positive rates: pooled DOH using their summed rate, not a claim
  that stock can physically move between towns/products.

This is historical cover for the latest **selected cohort with known demand**,
not a promise of consumption before expiry. DOH column sorting is available in
both status reports; the footer is recalculated from the selected stock and distinct rates.

## Click-to-explain overlay

DOH cells in town/brand status hierarchy rows, their national footer and batch rows open an
actual-data explanation. `ui/doh_data.py` serializes compact observed monthly
traces; `ui/doh_overlay.js` renders the original/adjusted sales line chart, monthly
actions and the clicked stock → adjusted monthly rate → DOH arithmetic. A
town-parent selector narrows the evidence without changing the clicked aggregate
calculation. Whole-cell clicks, keyboard activation, focus return and Escape are
supported; other movement cells still open their stock waterfalls.

`seasonal_supported` is a pipeline evidence tag, not a new rate transformation:

```text
unseasoned_upper_kl = local_positive_month_median × upper_residual
seasonal_supported = unchanged, sufficient supported history, season_factor > 1,
                     raw_kl > unseasoned_upper_kl and raw_kl <= upper_kl
```

These are **seasonal patterns allowed**: the seasonal uplift raised the bound and
the original sales were retained in full. The overlay shows the peer count,
factor and before/after caps. It does not label ambiguous preserved peaks as
seasonal or promise recurring annual seasonality. Rates and dependent measures
remain unchanged by the addition of these tags.

The chart uses calendar spacing and breaks at months absent from the entire
source. The supplied data has 12 distinct source months spanning July 2025–August
2026, with August 2025 and July 2026 absent globally. Those global gaps are not in
the legacy rate divisor. A town-parent missing within one of the supplied months
has an implicit zero contribution, labelled separately from observed zero. No
source months or demand are fabricated.

Verify with `node validation-artifacts/verify-doh-overlay.cjs`; gate evidence and
screenshots are in `validation-artifacts/doh-overlay-*`.
