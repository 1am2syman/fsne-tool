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

The full-page overlay carries the selected measure, BDT/KL display, report date and available Brand, Parent and Town dimensions. Its dropdowns stay within that report scope: Town, Parent and Material options reflect the other active filters and the report date. **Clear filter from…** restores that field's report bound; **Clear filters** restores all opened report bounds; they do not broaden a brand or town drill-down to the entire company. Material options show the description and exact child code, and filter by that code. A total omits row dimensions that do not apply. The report RSL condition filters each saved snapshot at this first level. Closing the overlay returns to the report with its expanded rows, selection and scroll intact; the standalone evolution chart retains its previous filter state.

A snapshot is closing stock for its report month and appears as the **next month’s opening**. For example, an August snapshot appears as September opening stock. The report date limits which saved snapshots are available.

Current report forecasts can use refreshed run rates, while evolution uses frozen snapshot measures. Their final amounts can therefore differ, especially for >10M Exposure in loading-watch. Mitigation lenses also leave out good stock and unclassified rows; the chart note shows those excluded amounts.

## Follow selected stock through RSL groups and categories

Right-click a mitigation category or flow band and choose **Show RSL evolution**. Right-click an RSL group or band and choose **Show category evolution**. The same menu works inside either selected view, so each further drill follows only stock from its parent selection. Keyboard users can focus a node or band and press **Shift+F10**. Clicking without opening the menu locks the highlighted flows.

These views keep the original measure (Exposure, LND FC or Closing stock), units, report date and Town, Parent, Material and Brand scope. The original RSL filter selects the first anchor quantity; it does not restrict that quantity’s RSL groups in other snapshots. The backend resolves every step from complete source flows, independently of truncated hover summaries.

Blended Sales push/Rotation categories retain their selected share. If you select the Sales push half, returning to categories at that anchor still shows only Sales push. Other months use their saved classifications. Carry, exit and added bands retain their own conditioning. Later stock increases do not enter the selected quantity. Category drill-downs explicitly show **Good stock** and **Unclassified** where present, so switching from RSL does not drop part of the selection. Flow allocation describes the model’s batch-level proportional attribution, not physical tracking of individual items.

Selected views hide the lens and scope controls to keep the selection fixed. The toolbar shows the selection trail; hover it to read the full trail. **Back to categories** or **Back to RSL** restores the immediate parent graph, filters, selected node/band, keyboard focus, spacing and scroll. Back remains available during loading or after an error; **Retry** repeats the complete selected request. Superseded requests cannot replace the active view after Back or Close. A trail supports up to 32 selections; at that limit, go Back to choose another branch.

## Show data behind a node or band

Every evolution chart, including the standalone page, report-cell overlays and selected RSL/category views, adds **Show data** to the right-click menu for any node or band. It opens a full-screen overlay that is the brand-status page itself — same filters, metric, sort, expand, stock trend and DOH explanation — limited to the brand · parent · town rows whose batch lineages sit behind the chosen node or band. The default order is Brand → Parent → Town; the order selector also offers Brand → Town → Parent and Town → Brand → Parent. The metric starts on the chart's lens (Exposure, L&D FC or Closing stock).

Rows show each row's full latest saved stock, not only the share that flows through the selection, and the header says so. Lineages below the chart's tiny-lineage floor are not listed, so rows can be fewer than the selection's lineage count. **Back to chart** or **Esc** returns to the chart with its selection intact; the brand-status page keeps its own filters and view.

## Lenses, display and export

| Lens | Measure and grouping |
| --- | --- |
| Exposure | Exposure by mitigation category |
| L&D FC | Forecast L&D by mitigation category |
| Closing stock | Closing stock by mitigation category |
| RSL | Closing stock by RSL group, including good stock |
| >10M cohort | Eligible Exposure originating in >10M, retained while eligible |

The standalone chart offers **Brand**, **Parent SKU**, **Town**, **Material** and **RSL** filters, in that order. Each dropdown lists options compatible with the other selections and provides the same Excel-style search, checkboxes, Select All, **OK** and **Cancel** as the report filters. Empty selections cannot be applied, and incompatible selections remain visible until explicitly changed. See [Report filters](../README.md#report-filters) for search and clearing behavior. The chart keeps separate timeline columns even when filtered stock disappears and reappears in disconnected periods; label widths set a minimum column spacing so tight spacing cannot overlap adjacent labels. Mitigation lenses exclude good stock and unclassified amounts. Sales attribution uses secondary-sales inputs when available; unavailable sales are shown explicitly.

Report overlays display **M BDT** or **KL**. Standalone BDT uses **crore BDT**. API graph values are BDT or litres; consumers must apply `unit_div` and `unit_label`, rather than treating raw `unit=kl` values as displayed KL. The report overlay deliberately overrides the BDT divisor to 1,000,000.

**Export CSV** exports the current graph’s flow links, filter scope and report date. Selected views also include `selection_id`, `selection_month` (the latest anchor snapshot date) and the full structured `selection_path`. Amount column names identify M BDT, crore BDT or KL.

## Runtime and API

- `src/evolution.py` loads saved history, classifies rows, builds flows and selected histories in both groupings, and caches graph/detail payloads.
- `ui/template.html` owns report-cell scopes, the dialog and lazy asset loading. Edit this template rather than generated `ui/index.html`.
- `ui/report_filters.js` owns the shared searchable checkbox dropdown and its draft/apply behavior.
- `ui/evolution/page.js` connects those dropdowns to bounded catalogs and owns data requests, selection state, Back navigation and CSV export.
- `ui/evolution/refchart.js` and `refchart.css` own the Sankey renderer and interactions. The adapted renderer is maintained here; no external generator is required.
- `server/app.py` serves the local assets and gzip JSON with ETag revalidation. Each run uses its own frozen `history/` and `input/`; sample endpoints use the configured shared history and bundled sample inputs.

The assets include D3 **7.9.2** and d3-sankey **0.12.3**, with their licenses beside the scripts. There is no runtime CDN dependency.

Run routes start at `/api/runs/<id>/evolution/`. Sample routes start at `/api/evolution/`. Both expose:

| Route suffix | Result |
| --- | --- |
| `filters` | Brand, Parent, Town, Material and RSL catalogs |
| `<lens>` | Graph summary |
| `<lens>/detail` | Lineage details for that graph |
| `<lens>/selection` | Selected history, alternating RSL and category grouping at each step |
| `<lens>/selection/detail` | Details for that selected history |
| `<lens>/rsl` | Original single-step category-to-RSL route |
| `<lens>/rsl/detail` | Details for the original single-step route |

Lens IDs are `exposure`, `lnd`, `stock`, `rsl` and `cohort`. Graph and catalog queries accept repeated `town`, `parent`, `material` (exact child code), `brand` and `rsl` values, and optional `as_of=YYYY-MM-DD`. The `filters` route intersects those conditions and returns arrays plus `material_labels` keyed by child code. Graph queries also accept `unit=bdt` or `unit=kl`.

Selected-history routes require `path`, a URL-encoded JSON array of 1–32 steps. Each step is either `{"node":"category:M2:sales_push"}` or `{"source":"category:M2:sales_push","target":"month:M3"}`. The first step refers to the base lens, and each subsequent step refers to the preceding selected graph. Grouping alternates automatically. RSL node IDs such as `category:M1:rsl:>10M` are passed intact; do not split IDs at `>`. A first-level selection from the RSL view with >10M hidden includes `"hide10":true`, so displayed carry/added bands select only their visible destinations. The synthetic **Transitioned from >10M** source maps to its original >10M carry band with that flag. The legacy `/rsl` route accepts `selection`, the original category node/link ID. Graph and detail requests must use identical parameters. Cache identity includes the complete path.

Parsed history lives in ignored `.cache/evolution/` (override with `FSNE_CACHE_DIR`). Graphs live under `output/evolution/cached/`, or the run’s `output/evolution/cached/`. Cache directory names include the input signature and complete report/selection scope. A schema or input change writes to a new directory; it does not require deleting the old cache first. Old root-level and `filtered/` graph caches are ignored after updating. Retention bounds ready directories where Windows permits deletion; locked old files and denied timestamp updates do not block a graph. Active reads and builds remain protected from eviction. Parsed-history cleanup also skips locked obsolete files. Cache generation never modifies saved reports.

After pulling an update, close the FSNE command window, start `start-fsne.bat` again, and refresh the browser. Existing runs are rebuilt into the new cache automatically; you do not need to delete runs, history or uploaded files. If a required file read/write is denied, the graph asks you to check the FSNE command window, where the traceback identifies the file and operation. This also covers reading run metadata before the graph builder starts. Close any other FSNE instance and retry; persistent errors may require correcting permissions on that folder.

`ConnectionAbortedError` / Windows error 10053 while sending a response means the local connection was aborted. It does not identify a file-permission failure or explain what aborted the connection. The server closes that connection without attempting another response. For a graph that still reports file access denied, capture the file-access traceback produced when you click **Retry**.

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
node --unhandled-rejections=strict --test tests/report_filters.test.cjs tests/doh_summary.test.cjs tests/evolution_layout.test.cjs tests/evolution_page.test.cjs
```

The regression suite covers intersecting scopes, units, report dates, full-path cache isolation, sample/run HTTP routes, exact forward/reverse/nested quantities, blended anchor shares, good/unclassified stock, >10M identifiers, and exclusion of later growth. Cache regressions inject Windows-style permission failures for obsolete files, retention, timestamps and required writes; they cover schema upgrades, warm-build reuse and active-read protection. These injections run on Linux and do not replace verification on a Windows machine. Page tests cover parent restoration, delayed responses after Back/Close, Retry and CSV provenance.

For UI changes, validate at widescreen laptop widths. In both BDT and KL, check Exposure and LND from all three action-plan reports, including expanded rows and totals. Drill from a category and a band, compare the anchor to the selected quantity, then verify Back and Close restore their prior views. Include brand/town status cells, keyboard menus, CSV provenance, Retry and a delayed response after Back or Close. Capture and inspect screenshots. Mobile/tablet and `tests/e2e_*` are outside routine UI validation.
