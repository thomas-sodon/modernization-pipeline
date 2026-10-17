# Report fixtures

Hand-written, synthetic inputs for `toolkit/tests/test_report.py` (S6.report). Nothing here comes
from a client or Microsoft file.

- `engagement.json`, `paths.local.json`: engagement `fixture-eng` (kind accdb, slug `shop`,
  PostgreSQL primary, SQL Server verify-also). The test copies the tree into `tmp_path` and replaces
  `/ROOT` in `paths.local.json` with it.
- `target/`: the engagement root. `goldens/manifest.json` names 6 kept units (`qA`..`qF`, areas
  `orders` and `stock`) and two seeds: `qE` is empty on seed-1 and `qF` is unsupported (crosstab)
  on seed-2, so |C_engagement| = 6, |C_seed-1| = 5, |C_seed-2| = 5; `qB` and `qE` are flagged
  needs_access_confirmation, which the report must disclose. `analysis/rules.json`,
  `docs/known-differences.json` (no entries) and `app/shop/*/tests/test_rules.py` (rule ids in
  docstrings) go with it. `conftest.py` stops pytest from collecting the fixture tree.
- `private/out/inventory.json`: design-zone inventory counts.
- `junit/equivalence/`: one passing JUnit file per cell (engagement, seed-1, seed-2 on postgresql
  and mssql).
- `junit/app/`: the app's own runs. PostgreSQL has rule tests, an end-to-end test and a view test;
  SQL Server has no end-to-end test, so it gives a rules cell only.
- `variants/junit-equivalence-seed-2-mssql.xml`: the seed-2/mssql cell with one failed (its message
  holds the canary `CANARY-1`), one error, one skipped, one not-implemented and one passed unit.
