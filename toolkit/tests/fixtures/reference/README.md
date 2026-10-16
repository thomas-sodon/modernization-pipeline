# Reference engine fixtures

Hand-written synthetic fixtures for `toolkit/tests/test_reference.py` and
`toolkit/tests/test_bridge_sources.py`. Nothing here is copied from a client file or from
Microsoft's Northwind; the names and values are invented.

- `model/`: a tiny object model (`schema.json`, `objects.json`, `procedures.json`) with query
  designs for a join, an outer join, a group by with having, a declared parameter, a union (as
  design parts and as SQL text), a query on a query, a form reference, an app-function call with
  a rewrite and one without, an action query and a crosstab.
- `functions/OpenAmount.json`: the correlated-subquery rewrite of the synthetic app function.
- `dataset/`: the rows of `model/schema.json` as a dataset directory (written with
  `toolkit.dataset.write_dataset`).
- `bridge_sources/`: two related tables (`schema.json`), their rows as a dataset directory and
  the table parts of an `.accdt` (`accdt/*.xsd`, `accdt/relationships.xml`); the test assembles
  the `.accdt` and creates the `.accdb` and `.mdb` files at run time.
