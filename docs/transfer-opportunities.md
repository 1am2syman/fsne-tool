# Beneficial transfer plans

The transfer report compares **leave the batch where it is** with **move part of it**. A partial improvement is useful; crossing the 300-day threshold does not automatically disqualify a transfer.

## What stays unchanged

The saved report's exposure calculation and 300-day risk trigger are unchanged. Reported exposure selects source batches and caps their transfer quantity; it is not replaced with a new exposure definition. A physical stock transfer alone is not a saving or a reduction in network stock.

## Two clearly separated comparisons

- **More than 300 days remaining:** assess both choices on the day the batch reaches 300 days remaining (`rsl_days - 300`). Show how much less stock remains at that checkpoint.
- **300 days or less remaining:** assess both choices at the batch's expiry (`rsl_days`). Show improved sell-through and residual unsold stock. Never claim the existing threshold breach has been reversed.

A positive residual after a transfer remains marked as needing another action. No sales occur on or after expiry. Thirty days represents one month, matching the other scenario reports.

## Demand and candidate towns

`src/transfer_opportunities.py` calculates the plan; `ui/transfer_data.py` only serializes it. Candidate destinations include all other towns with current stock or historical sales of the **exact material code**, including towns with no current stock. Towns without either evidence are not assumed to sell a new material. Town/channel names are not geographical routing inputs.

Demand uses the adjusted town-parent rate split **once** by each child's share of that parent's historical secondary sales over the same supplied window. Related pack sizes do not each receive the full parent rate. Missing parent demand remains unknown; a known parent rate with no child sales gives that child zero demand. Production loads the same source-date window as the current report. Without explicit child demand, the public transform allows only single-child families and treats multi-child families as unknown.

## Conservative shared plan

1. Process source batches by highest reported exposure value, then town and batch id. Process destinations by highest exact-material sales rate, then town. The plan is greedy, not globally optimal.
2. Project earliest-expiry-first sales within each town and exact material; ties use stable batch identity. The same sales rate serves every batch in that pool. All earlier accepted transfers remain in the working pools.
3. Move only stock projected to remain at the source at the assessment date, capped by the source stock and reported exposure.
4. Reserve sales for **all** exact-material stock already at the destination, including earlier planned transfers. Only remaining sales can support another transfer. This intentionally conservative bound can miss other workable transfers.
5. Reduce the proposed quantity if it would worsen any destination batch's remaining stock at that batch's own 300-day checkpoint (or expiry if already breached). This prevents a later assessment hiding an earlier problem.
6. Simulate leave versus move at the **same** source assessment date and require a positive reduction in combined stock left in both towns. Otherwise reject. Accepted moves update stock and shared demand for subsequent suggestions.

Expired stock remains recorded as unsold, not silently deleted from the comparison. Unknown source demand blocks a comparison rather than being treated as zero. No destination is rejected merely because it has other at-risk stock.

## UI and export

The source table shows the unchanged reported exposure, assessment date/horizon, batch remaining if kept versus with the complete plan (tracked across all receiving towns), suggested volume and unresolved status. Destination rows show all exact-material stock left across the two affected towns immediately before and after each sequential transfer step, plus net improvement. These two-town populations overlap; they must not be added. Different source assessment dates also must not be added into a portfolio benefit.

The BDT/KL toggle changes **reported exposure only**. Modeled quantities stay in KL, not cash savings. Filters choose sources and assessments; they never recompute the shared plan or release reservations. CSV exports every filtered source, including rejected sources, with comparison fields, reason counts and policy version. Rejection counts distinguish missing demand, local sell-through, lack of spare sales, displacement of other risk and exhausted source allocation.

`output/transfer_comparison.parquet` persists the complete candidate ledger used by the UI. Policy: `transfer-benefit-v2`.

## Limits requiring human review

Transfers are assumed immediate at the snapshot. There are no route, lead-time, transport-cost, customer acceptance, open-order, in-transit, pack-rounding or service-reserve inputs. Rates are historical, not guaranteed future demand. New receipts, promotion uplift and seasonal changes are not modeled. The stock/price basis of the saved exposure measure differs from the shared-child scenario: they are deliberately presented separately. The 300-day trigger is not a selling cutoff; expiry is not a promise of customer acceptance until that date.
