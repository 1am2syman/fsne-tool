# Task: translate Excel Power Query queries to Polars

Goal: Translate all eight queries in `queries/` into chained native Polars scripts in `src/`, resolving input and output paths with pathlib relative to the repository.

Plan: shared file/header/date adapters; eight individual transformation and loading modules; CLI integration; synthetic behavior and file integration tests; final source-step review and gate verification.

Decisions: no production workbooks supplied; use generated fixtures and disclose that real-data parity is unverified. Preserve recursive folder matching and combined-header promotion. Preserve differing batch-key whitespace behavior in soh_db and sec_history. Compute run-rate averages as grouped monthly sums divided by global distinct month count (zero-filled pivot equivalent). Excel sheets and named tables are read with Polars plus openpyxl at the workbook metadata boundary.

Progress: read all original queries and existing project files; acceptance gates written. Implementation pending.

Safety: do not modify original queries or existing unrelated files; no row-wise Python transformation UDFs; no hardcoded absolute machine paths.
