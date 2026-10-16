"""Target-neutral equivalence harness (docs/contracts.md#harness).

Vendored into the target repo as `equivalence/` by `toolkit vendor-harness`. Standard library,
pytest, datacompy and pytest-regressions only; it never imports toolkit. Modules import each other
relatively, so the package works as pipeline `harness` and as vendored `equivalence`.

- `plugin`: the pytest plugin (`-p equivalence.plugin`) and its options.
- `adapter`: the adapter protocol and `NotImplementedUnit`.
- `compare`: canonical bytes, digests and mismatch counts (datacompy).
- `redact`: redaction of reports on the engagement dataset.
- `canonical` (S2.golden) and `dataset` (S0.dataset).
- `tests/test_units.py`: the parametrised runner.
"""
