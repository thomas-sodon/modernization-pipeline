# golden fixtures

Hand-written synthetic fixtures for toolkit/tests/test_golden.py (a made-up widget shop). Nothing
here is copied from a client or Microsoft file.

- catalog.json and sql/: a reference catalog with a kept ok unit, a kept needs-params unit
  (form reference), a kept unsupported unit, a kept unit that returns no rows, a dropped unit and
  a unit whose object rules.json does not list.
- rules.json: the matching merged rules (one area, sales).
- dataset/: a dataset directory (Customers, Orders) used as seed-1 and, renamed, as the
  engagement dataset.
- status/: a fixture manifest and rules for golden status rates.
