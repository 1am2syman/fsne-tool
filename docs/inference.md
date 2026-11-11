# Inferred final-report rules

## Status and evidence

**Current policy:** production run rates now use seasonality-aware upper-tail winsorization. The sample-equivalence evidence below describes the unadjusted baseline, not the adjusted report. See [run-rates.md](run-rates.md) for the policy, limitations, DOH column and current audit artifacts. Verify baseline parity with `uv run python -m src.compare_report --legacy --output-dir output/legacy-compare`.

The user authorized inference because the final M/DAX/calculated-column formulas were not supplied. `src/report.py` generates the report solely from `sample_input/`; only `src/compare_report.py` reads the reference workbook afterwards.

Full-sample verification checks every cell in `sample_output/sample_output.xlsx`: 27,891 rows × 30 columns = 836,730 cells. Strings, integer identifiers/date serials, column order, row order and null placement match. Floating-point comparisons use `abs(actual - expected) <= 1e-8 + 1e-12 * abs(expected)`; the observed largest absolute difference is below 1e-9. This is **sample equivalence under numerical tolerance, not bitwise equality or proof of the original business rules**.

Generated artifacts:

- `output/inferred_report.xlsx` — generated data, `Sheet1`; not the original Excel formatting.
- `output/inferred_report.parquet` — same data before Excel serialization.
- `output/comparison.csv` — per-column row count, null/value mismatch counts and maximum absolute errors.
- `output/comparison.json` — dimensions, tolerance, row-order check, Excel roundtrip comparison and SHA-256 input/reference integrity checks.
- `output/current_measures.json` — current-period totals supported by the available input data.

## Base quantities and dates

For each batch identity:

- `report_date` = `config_table.starting_date`, exported as an Excel integer serial.
- `Expiry Date` = original expiry, likewise exported as an Excel integer serial.
- `rsl_days` = expiry date − report date in calendar days.
- `total_closing_stock_kl` = sum of CSV `Closing Stock`.
- `total_pri_sales` / `total_sec_sales` = sums of CSV primary/secondary sales.
- `avg_sec_m` = town/parent monthly rate from `run_rate_parent` (NOT the CSV last-six-month average).
- `doh` = `total_closing_stock_kl / avg_sec_m * 30`.
- `total_closing_stock_bdt` = sum of the CSV `DB Landing value` for the batch (that column is `NOS` × the row's landing rate). The reference workbook used a separate per-material landing-rate workbook instead; see [stock-valuation.md](stock-valuation.md) for why the pipeline changed.

Remaining-shelf-life groups:

| Days | Label |
| --- | --- |
| ≤ 90 | `< 3M` |
| 91–180 | `3M - 6M` |
| 181–300 | `6M - 10M` |
| > 300 | `> 10M` |

Exact boundaries are inferred: the sample contains 180 days, but not every threshold endpoint.

## Batch schedule: the consequential inferred rule

Process batches separately for each **town + child material code**, ordered by expiry then batch key. The parent rate is reused for each child; a shared parent queue does not reproduce the sample.

For stock `S`, monthly rate `R`, reserve `b` days, multiplier `m`:

```text
horizon_i = max(rsl_days_i - b, 0)
duration_i = min(max(rsl_days_i - 90, 0), (S_i / R_i * 30) / m)
start_i = floor(report_date_serial + sum(duration_j for j before i))
available_i = max(expiry_serial_i - b - start_i, 0)
consumed_i = min(S_i, available_i / 30 * R_i * m)
```

**Approved queue correction (2026-11-11).** Both deadlines now use the same 90-day selling queue at a given rate. Older stock continues to block newer stock below 300 days RSL until it sells out or reaches 90 days. The previous reference-matching formula used each output's own reserve to build its queue; that incorrectly let Exposure fall below LND FC on some >10M batches.

Durations still have independent caps measured from the report date, rather than being recalculated from each preceding batch's actual finish time. This preserves the existing LND FC calculation; it is not an ideal FIFO simulation.

**Do not truncate individual durations.** Fractional durations are accumulated first. Truncate the resulting absolute Excel date serial to whole days. Truncating each duration or subtracting preceding stock volumes does not reproduce the sample.

Three outputs use two queues (1× and 2×), each with 90-day duration caps. Their own evaluation deadlines remain:

| Output | Reserve | Rate multiplier |
| --- | --- | --- |
| `consumable_kl` | 300 days | 1 |
| `consumable_kl_2x_rate` | 300 days | 2 |
| quantity consumed before forecast cutoff | 90 days | 1 |

For missing rate `R`, inferred consumption is the full stock if remaining shelf life exceeds the reserve; otherwise it is zero. `doh` and `avg_sec_m` remain null. In the supplied sample this applies to 1,250 rows.

## Exposure, rotation and forecast

```text
exposure_kl = S - consumable_kl
rotation_req_kl = S - consumable_kl_2x_rate
lnd_fc_kl = S - consumed_before_90_day_cutoff
```

Each BDT quantity is its KL quantity divided by total stock KL, multiplied by total stock BDT. A blank `DB Landing value` propagates null to BDT results, including BDT ratios; the supplied sample has none.

Percentage columns are fractions, not numbers multiplied by 100:

```text
exposure_kl_% = exposure_kl / S
exposure_bdt_% = exposure_bdt / total_closing_stock_bdt
lnd_fc_kl_% = lnd_fc_kl / S
lnd_fc_bdt_% = lnd_fc_bdt / total_closing_stock_bdt
```

Final rows sort by `batch_index`; final columns match the reference order.

## What remains assumed or unavailable

- There is only one reference dataset. Matching all its values does not uniquely identify the original formulas. These rules need business-owner review and another independent period's golden output.
- The sample has one row per batch key. Combining duplicate distributor rows by batch identity and ordering equal-expiry ties by batch key are documented extrapolations, not established original rules.
- Negative/zero stock, zero/negative or nonfinite rates, pre-expired stock, malformed source dates, unusual cultures and 1904-system serials are not established by this sample. The current report rejects nonpositive stock and negative/nonfinite non-null sales rates. By the approved DOH policy, a known zero rate consumes no stock and retains null batch `doh` for portable export; the UI renders ∞ from its retained zero rate.
- The original eight query scripts remain distinct from the inferred final report. `history` can load historical outputs but no historical workbooks were supplied.
- `measures.png` contains historical comparison aggregates, not formulas. Last-month exposure, new-inclusion/no-new-inclusion splits, down-transition, incumbent stock and month-to-month reductions **cannot be reconstructed from the single supplied period alone**.
- The original reference used the former queue rule. With current rates and shelf-life valuation, corrected sample Exposure is 42.6931 KL / 38,788,056.10 BDT. LND FC is unchanged. `src.compare_report --legacy` verifies every non-valuation, non-queue column against the reference; queue differences are reported explicitly.
- Saved months and completed runs retain their stored Exposure and Rotation req. The first newly calculated month therefore compares methods once; this is a method change, not a sales outcome. Rebuilding a frozen dashboard alone does not recalculate its report.
- Excel export matches contents, not original formatting, table metadata or workbook binary bytes.

## Reproduce

```bash
uv run python -m src report --data-dir sample_input --output output/inferred_report.xlsx
uv run python -m src.compare_report --legacy --output-dir output/legacy-compare
uv run python -m unittest discover -s tests -v
```

Never use the expected output as input to report generation or overwrite the reference to make a comparison pass.
