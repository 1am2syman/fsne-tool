# Gates: Power Query to Polars

OWNS: src/**, tests/**, pyproject.toml, uv.lock, README.md, GATES.md, TASK.md

Scope: Convert all queries into runnable chained, vectorized Polars pipelines with repository-relative pathlib inputs.

- [ ] G1: Every source query has an importable Python module and transformations avoid row-wise Python UDFs.
  CHECK: uv run python -m unittest tests.test_queries.StructureTests -v
  EXPECT: OK
  EVIDENCE: pending

- [ ] G2: Synthetic transformation and Excel/CSV integration tests pass, including query dependencies and relative CLI paths.
  CHECK: uv run python -m unittest discover -s tests -v
  EXPECT: OK
  EVIDENCE: pending

- [ ] G3: Original M steps are reviewed against translations and limitations documented.
  EVIDENCE: pending
