# Gates: inferred final report

OWNS: src/report.py, src/compare_report.py, src/cli.py, src/__main__.py, src/io.py, src/db_landing.py, tests/test_report.py, docs/**, output/**, pyproject.toml, uv.lock, README.md, TASK.md, INFERENCE_GATES.md

Scope: Infer and implement the final sample report using native Polars, preserve references, record full-sample mismatches and unsupported generalizations.

- [x] I1: Inferred formulas pass independent boundary and invariance tests, existing tests, lint and types.
  CHECK: uv run python -m unittest discover -s tests -v && uvx ruff check src tests && uvx pyright --project pyrightconfig.json
  EXPECT: 0 errors, 0 warnings
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=d15fd4d28cb2/10 entries; output=Ran 22 tests in 0.548s | OK

- [x] I2: Sample report is generated without using reference data, Excel output roundtrips, and comparison records every column and row.
  CHECK: uv run python -m src.compare_report
  EXPECT: COMPARISON_COMPLETE
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=d15fd4d28cb2/10 entries; output=/home/acer_vps/GitHub/fsne-tool/src/compare_report.py:105: FutureWarning: from_arrow(<ArrowStreamExportable>) will return a Series instead of a DataFrame in 2.0. To avoid this warning, pass the ArrowStreamExportable to either `pl.DataFrame`

- [x] I3: Inferred assumptions, numerical tolerances, remaining mismatches and unavailable history-based measures are documented honestly.
  EVIDENCE: docs/inference.md records formulas and counterexamples to ideal FIFO and per-row day truncation, reserve boundaries, missing-rate behavior, child-material queue isolation, native Excel rate precision, duplicate/tie extrapolations and rejected domains. README documents regeneration and complete comparison. Historical screenshot measures requiring prior periods are explicitly unavailable. Initial full comparison found zero mismatches across 836730 cells under 1e-8 + 1e-12*abs(expected) tolerance; no claim of original-formula proof or binary workbook equivalence.
