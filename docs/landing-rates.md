# Landing-rate dependency audit

The landing-rate workbook is required by the current calculation. It provides one `DB Landing rate` per material code. `src/db_landing.py` reads it; `src/report.py::transform` joins the rates to batch stock by material code.

The valuation formula is:

```text
total_closing_stock_bdt = sum(NOS) × material DB Landing rate
consumable_bdt          = consumable_kl / total_closing_stock_kl × total_closing_stock_bdt
rotation_req_bdt        = rotation_req_kl / total_closing_stock_kl × total_closing_stock_bdt
exposure_bdt            = exposure_kl / total_closing_stock_kl × total_closing_stock_bdt
lnd_fc_bdt              = lnd_fc_kl / total_closing_stock_kl × total_closing_stock_bdt
```

The shelf-life CSV's `DB Landing value` is ingested and cast by `src/soh_db.py`, but it is not used for these valuations. The calculation recomputes value from NOS and the separate rate workbook.

Downstream uses:

- `ui/run_rate_data.py` calculates both current and baseline reports with the workbook rates and validates stock value against the saved snapshot.
- Dashboard stock/exposure/rotation/liquidation values, BDT sorting, saved snapshots, and run summary totals/change measures consume those report values. Historical movement uses saved values; changing today's rate workbook does not rewrite prior snapshots.
- `server/runs.py::run_summary` reads the workbook again to count batches missing a rate.
- `src/inventory_projection.py` values projected remaining KL proportionally to opening stock BDT.
- `src/transfer_opportunities.py::transform` processes sources in descending `source_exposure_bdt`. Because accepted transfers update shared pools, different relative material rates can change processing order and consequently the suggested plan, even though its demand/capacity arithmetic uses KL.
- `src/sales_push_feasibility.py::build` loads the workbook through report generation. The feasibility solver itself does not value its scenarios; it uses physical quantities, sales rates and dates.
- `src/compare_report.py` also supplies the workbook to report generation for validation.

Rates do not directly determine stock KL, sales rates, DOH, remaining shelf life, consumption KL, exposure KL, or rotation KL in `report.transform`. `tests/test_upload_samples.py::test_landing_rate_changes_values_but_not_stock_or_consumption_volumes` demonstrates this with real sample schemas: doubling the rate doubles the BDT amount columns and preserves the tested physical quantities/DOH. Missing material rates produce null BDT values; duplicate material rates are rejected.

Configuration is independent: `src/starting_date.py` consumes `starting_date` from `config_table`. The upload UI exposes it as Report date. Run label is metadata. The form saves a private configuration workbook for the existing pipeline; a user-supplied workbook is optional.
