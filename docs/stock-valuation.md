# Stock valuation (BDT)

Stock value comes from one place: the shelf-life report's `DB Landing value` column. A batch's `total_closing_stock_bdt` is the sum of that column over its rows. A blank value makes the batch value null; a non-finite value is rejected.

```text
consumable_bdt   = consumable_kl   / total_closing_stock_kl × total_closing_stock_bdt
rotation_req_bdt = rotation_req_kl / total_closing_stock_kl × total_closing_stock_bdt
exposure_bdt     = exposure_kl     / total_closing_stock_kl × total_closing_stock_bdt
lnd_fc_bdt       = lnd_fc_kl       / total_closing_stock_kl × total_closing_stock_bdt
```

Rates affect only BDT columns and anything ranked by BDT (transfer source priority, dashboard BDT sorting, run summary totals, projected value). They do not affect KL, DOH, sales rates or shelf-life groups.

## Why the landing-rate workbook was retired

The earlier method multiplied `NOS` by a per-material `DB Landing rate` from a separate workbook. On the supplied sample (27,891 batch rows) the shelf-life file already carried the value, and it equals `NOS × DB Landing Rate` on every row to within 0.0005 BDT. The workbook disagreed with it:

| | Shelf-life `DB Landing value` | Landing workbook |
| --- | --- | --- |
| Total stock value | 1,325.07M BDT | 1,234.56M BDT |
| Rows without a rate | 0 | 1,529 rows (27 materials, 90.4M BDT) |
| Materials with obviously wrong rates | none | e.g. 732758: 0.0097 against 538.11 |

The workbook's rate is a period average (`DISTRIBUTOR_LANDED_PRICE ÷ SALES_QTY`) per material; the shelf-life file is per SKU/batch row. Two materials carry two different rates in the shelf-life file, which a single per-material rate cannot represent. Evidence: `validation-artifacts/landing-comparison/`.

## Consequences

- Monthly upload needs the shelf-life report, secondary sales and product hierarchy, plus the report date.
- `sample_output/sample_output.xlsx` was valued with the workbook and used the former per-deadline queue. `python -m src.compare_report --legacy` checks all unaffected columns; valuation and the approved queue correction are reported separately (`valuation_columns_differing`, `queue_columns_differing`). See [inference.md](inference.md) for the shared 90-day queue.
- Saved snapshots from earlier months keep their stored BDT values. Month-on-month BDT changes across the switch compare different valuation bases once.
- Zero values in the shelf-life file (352 sample rows) are treated as real zeros. One of them, material 725335, disagrees with the old workbook (148.26); check it with the data owner.
