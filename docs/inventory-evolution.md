# Inventory evolution and report drill-downs

Inventory evolution follows saved inventory between report snapshots. Open **Reports → inventory-evolution**, or enter from a report cell to carry its measure and row scope into the chart. It reads saved figures without recalculating or repricing historical reports.

## Open from a report

Right-click an **Exposure** or **LND FC** cell and choose **Show evolution**. The action also works on expanded parent/town rows and totals. Select a sheet cell and press **Shift+F10** for the keyboard menu.

| Report | Initial RSL scope |
| --- | --- |
| rescue | 3M–6M |
| rebalance | 6M–10M |
| loading-watch | >10M |
| brand-status / town-status | Clicked RSL column, or all groups for a total; selected metric must be Exposure or LND |

The full-page overlay carries the selected measure, BDT/KL display, report date and available Brand, Parent and Town dimensions. Its dropdowns stay within that report scope: Town, Parent and Material options reflect the other active filters and the report date. **All in scope** and **Clear filters** restore the opened report's bounds; they do not broaden a brand or town drill-down to the entire company. Material options show the description and exact child code, and filter by that code. A total omits row dimensions that do not apply. The report RSL condition filters each saved snapshot at this first level. Closing the overlay returns to the report with its expanded rows, selection and scroll intact; the standalone evolution chart retains its previous filter state.

A snapshot is closing stock for its report month and appears as the **next month’s opening**. For example, an August snapshot appears as September opening stock. The report date limits which saved snapshots are available.

Current report forecasts can use refreshed run rates, while evolution uses frozen snapshot measures. Their final amounts can therefore differ, especially for >10M Exposure in loading-watch. Mitigation lenses also leave out good stock and unclassified rows; the chart note shows those excluded amounts.

## Follow a category or band through RSL groups

Right-click a mitigation category or flow band and choose **Show RSL evolution**. Keyboard users can focus a category or band and press **Shift+F10**.

This view follows the selected quantity, keeping its Exposure/LND measure, units, date, Brand, Parent and Town. The original RSL filter selects the anchor quantity; it does not restrict the selected quantity’s RSL groups in other snapshots. The backend resolves the selection from the complete source flow, independently of truncated hover summaries.

Blended Sales push/Rotation categories retain their selected share. Carry, exit and added bands retain their own conditioning. Later stock increases do not enter the selected quantity. Flow allocation describes the model’s batch-level proportional attribution, not physical tracking of individual items.

The RSL view hides the lens and scope controls to keep that selection fixed. **Back to categories** restores the original graph, filters, selected category/band, keyboard focus, spacing and scroll. Back remains available during loading or after an error; **Retry** repeats the selected request. Superseded requests cannot replace the active view after Back or Close.

## Lenses, display and export

| Lens | Measure and grouping |
| --- | --- |
| Exposure | Exposure by mitigation category |
| L&D FC | Forecast L&D by mitigation category |
| Closing stock | Closing stock by mitigation category |
| RSL | Closing stock by RSL group, including good stock |
| >10M cohort | Eligible Exposure originating in >10M, retained while eligible |

The standalone chart offers Town, Parent, Material, Brand and RSL filters. Each dropdown lists options compatible with the other selections. The chart keeps separate timeline columns even when filtered stock disappears and reappears in disconnected periods; label widths set a minimum column spacing so tight spacing cannot overlap adjacent labels. Mitigation lenses exclude good stock and unclassified amounts. Sales attribution uses secondary-sales inputs when available; unavailable sales are shown explicitly.

Report overlays display **M BDT** or **KL**. Standalone BDT uses **crore BDT**. API graph values are BDT or litres; consumers must apply `unit_div` and `unit_label`, rather than treating raw `unit=kl` values as displayed KL. The report overlay deliberately overrides the BDT divisor to 1,000,000.

**Export CSV** exports the current graph’s flow links, filter scope and report date. In the RSL drill-down it also includes `selection_id` and `selection_month` (the anchor snapshot date). Amount column names identify M BDT, crore BDT or KL.

## Runtime and API

- `src/evolution.py` loads saved history, classifies rows, builds flows and selected RSL histories, and caches graph/detail payloads.
- `ui/template.html` owns report-cell scopes, the dialog and lazy asset loading. Edit this template rather than generated `ui/index.html`.
- `ui/evolution/page.js` owns filters, data requests, selection state, Back navigation and CSV export.
- `ui/evolution/refchart.js` and `refchart.css` own the Sankey renderer and interactions. The adapted renderer is maintained here; no external generator is required.
- `server/app.py` serves the local assets and gzip JSON with ETag revalidation. Each run uses its own frozen `history/` and `input/`; sample endpoints use the configured shared history and bundled sample inputs.

The assets include D3 **7.9.2** and d3-sankey **0.12.3**, with their licenses beside the scripts. There is no runtime CDN dependency.

Run routes start at `/api/runs/<id>/evolution/`. Sample routes start at `/api/evolution/`. Both expose:

| Route suffix | Result |
| --- | --- |
| `filters` | Town, Parent, Brand and RSL catalogs |
| `<lens>` | Graph summary |
| `<lens>/detail` | Lineage details for that graph |
| `<lens>/rsl` | RSL graph for the selected category or band |
| `<lens>/rsl/detail` | Details for that selected RSL graph |

Lens IDs are `exposure`, `lnd`, `stock`, `rsl` and `cohort`. Graph and catalog queries accept repeated `town`, `parent`, `material` (exact child code), `brand` and `rsl` values, and optional `as_of=YYYY-MM-DD`. The `filters` route intersects those conditions and returns arrays plus `material_labels` keyed by child code. Graph queries also accept `unit=bdt` or `unit=kl`. RSL drill-down requests also require `selection`, the node/link ID from the category graph built with the same measure, units and scope. URL-encode filter values and IDs. Graph and detail requests must use identical parameters.

Parsed history lives in ignored `.cache/evolution/` (override with `FSNE_CACHE_DIR`). Graphs live in `output/evolution/`, or the run’s `output/evolution/`. Input signatures and the schema version invalidate stale payloads. Filtered scopes have bounded cache retention, with active reads protected from eviction. Cache generation does not modify saved reports.

The server builds missing graphs on demand. `ui/build.py` also warms all lenses and units. To warm only evolution artifacts:

```bash
POLARS_MAX_THREADS=2 uv run python -m src.evolution --history-dir history --data-dir sample_input --output-dir output/evolution
```

Serve the normal app for interactive charts; a copied static HTML file does not supply the evolution API. For template-only updates, preserve existing sample data with `uv run python -m ui.rerender ui/index.html`.

## Verification

```bash
POLARS_MAX_THREADS=2 uv run python -m unittest tests.test_evolution -v
node --check ui/evolution/page.js
node --check ui/evolution/refchart.js
node --test tests/evolution_layout.test.cjs
```

The regression suite covers intersecting scopes, units, report dates, cache isolation, run-scoped HTTP routes, selection quantities, RSL history and exclusion of later growth.

For UI changes, validate at widescreen laptop widths. In both BDT and KL, check Exposure and LND from all three action-plan reports, including expanded rows and totals. Drill from a category and a band, compare the anchor to the selected quantity, then verify Back and Close restore their prior views. Include brand/town status cells, keyboard menus, CSV provenance, Retry and a delayed response after Back or Close. Capture and inspect screenshots. Mobile/tablet and `tests/e2e_*` are outside routine UI validation.
