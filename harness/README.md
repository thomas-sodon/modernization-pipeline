# Equivalence harness

This folder holds the tests that prove the new app gives the same results as the original
application. Every kept query or document of the original app is a *unit*. Its recorded output, the
*golden*, is stored in `../goldens/`. The harness runs each unit through the new app, puts the
result into the same canonical form (`canonical.py`) and compares it with the golden.

It is a pytest plugin. It uses only the Python standard library, pytest, datacompy and
pytest-regressions, so it runs without the modernization toolkit.

## What is here

| File | Purpose |
|---|---|
| `plugin.py` | the pytest plugin (`-p equivalence.plugin`) and its options |
| `adapter.py` | the adapter protocol: `load_dataset`, `run_saved_query`, `render_document`, `NotImplementedUnit` |
| `compare.py` | canonical bytes, digest check and mismatch counts |
| `redact.py` | redaction of failures on the engagement data |
| `canonical.py`, `dataset.py` | the canonical output form and the dataset directory reader |
| `tests/test_units.py` | the runner: one test per unit |
| `adapter.toml` | how to start the app's adapter (written when the app is built) |
| `results.json` | the counts of the last full run (written at hand-over) |

## Re-running on the synthetic datasets

The synthetic datasets (`../goldens/synthetic/seed-N/`) contain only invented data. They run
anywhere, including in CI. From the app folder (`../app`):

```
uv run python -m pytest -c pyproject.toml --rootdir . ../equivalence/tests -p equivalence.plugin \
  --create-db --eq-adapter ../equivalence/adapter.toml --eq-dataset seed-1 --eq-db postgresql \
  --junitxml junit-equivalence-seed-1-postgresql.xml -p no:cacheprovider
```

- `--eq-db` is `postgresql` or `mssql`. When `DATABASE_URL` is unset, the plugin takes the server
  URL from the variable that `adapter.toml` names for that database (`PG_URL` or `MSSQL_URL`), for
  example `postgresql://user:password@127.0.0.1:5432`, and adds the app's database name.
- `--eq-only-areas orders,inventory` runs only the units of those areas and starts the app with
  only those areas installed (`MP_ONLY_AREAS`).
- `-k '[orders::'` selects one area by test id (`test_unit[<area>::<unit>::<dataset>::<db>]`).
- A unit the app does not implement yet is reported as not-implemented. It fails the run unless you
  pass `--eq-allow-not-implemented`.
- `--force-regen` and `--regen-all` are refused: goldens are never rewritten by a test run.

## Re-running on your own data

The goldens of your real data are not in this repository. Only their digests (HMAC-SHA256) are in
`../goldens/manifest.json`. To re-run on your data you need:

1. a folder, here called `PRIVATE`, holding `datasets/engagement/` (your data exported as a dataset
   directory) and `goldens/params.json` (the parameter values that were recorded on your data). We
   hand both over on request.
2. the data file the goldens were recorded from (the Access database). The digest key is derived
   from its bytes, so only someone with that file can check the digests.

Then, from the app folder:

```
EQUIVALENCE_PRIVATE=/path/to/PRIVATE EQUIVALENCE_DATA_FILE=/path/to/your-database-file \
uv run python -m pytest -c pyproject.toml --rootdir . ../equivalence/tests -p equivalence.plugin \
  --create-db --eq-adapter ../equivalence/adapter.toml --eq-dataset engagement --eq-db postgresql \
  --junitxml junit-equivalence-engagement-postgresql.xml -p no:cacheprovider
```

- The harness computes the key as HMAC-SHA256 with the key `modpipe-golden-v1` over the file's
  bytes (`canonical.key_from_file`). Instead of `EQUIVALENCE_DATA_FILE` you may pass the key itself
  as hex in `EQUIVALENCE_DIGEST_KEY`. The key is never printed or written.
- Failures on your data are redacted: the terminal and the JUnit file show only
  `<test>: <n> rows differ in columns [...]`. The full detail goes to `PRIVATE/logs/`, together with
  a datacompy report that has counts and column names but no values. If `PRIVATE/goldens/engagement/`
  holds the recorded outputs, that report counts the differences per column.
- When your data cannot be loaded, every test reports an error such as
  `load_dataset failed: LoadError (table Orders, column Notes, code blank)`: the error types and the
  table, column or constraint involved, never the values. The JUnit file holds the same names in the
  `equivalence.setup-error` property of each test.
- Options that would send the app's output past pytest's capture are refused on your data, so
  printed or logged values cannot reach the terminal: `-s` (`--capture=no`), `--capture=tee-sys`,
  live logging (`log_cli = true` in the app's pytest settings, or `--log-cli-level`), `--log-file`,
  `--pdb`, `--trace` and `--debug`. Read `PRIVATE/logs/` instead.
- Keep `PRIVATE` out of version control.
