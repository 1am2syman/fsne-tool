# Gates: Power Query to Polars

OWNS: src/**, tests/**, pyproject.toml, pyrightconfig.json, uv.lock, README.md, GATES.md, TASK.md

Scope: Convert all queries into runnable chained, vectorized Polars pipelines with repository-relative pathlib inputs.

- [x] G1: Every source query has an importable Python module and transformations avoid row-wise Python UDFs.
  CHECK: uv run python -m unittest tests.test_queries.StructureTests -v
  EXPECT: OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=9ecc81c2063f/11 entries; output=Ran 1 test in 0.019s | OK

- [x] G2: Synthetic transformation and Excel/CSV integration tests pass, including query dependencies and relative CLI paths.
  CHECK: uv run python -m unittest discover -s tests -v
  EXPECT: OK
  EVIDENCE: exit=0; shell=/bin/sh; cwd=/home/acer_vps/GitHub/fsne-tool; path=9ecc81c2063f/11 entries; output=Ran 22 tests in 0.729s | OK

- [x] G3: Original M steps are reviewed against translations and limitations documented.
  EVIDENCE: Reviewed all eight queries against matching transform/build modules. Confirmed source widths, header/filter order, 30 history casts, parent fallback/deduplication, global zero-filled month denominator, null-aware left joins, distinct batch-key whitespace rules, and config transpose. README documents locale, ordering, repeated-header behavior, named-range/quoted-delimiter limitations and lack of production-workbook parity. Generated-workbook integration covers all eight builds; Ruff and project Pyright both pass.
