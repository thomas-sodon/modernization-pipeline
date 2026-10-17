"""Equivalence units of the __area_title__ area (docs/contracts.md#harness).

Register exactly the unit ids of goldens/manifest.json whose area is __area__; a unit
nobody registers stays not-implemented. core.apps imports this module when Django
starts, so keep it importable at every step. Compute from the database through the
ORM and the domain functions (app/__slug__/domain/README.md); never read golden
files and never branch on the dataset, a seed or row ids.

One example per unit kind. Replace them with the area's units.

saved_query, a saved query of the original: `params` maps each parameter of
goldens/params.json to its canonical string; return one tuple per row with the
manifest's columns in order::

    @saved_query("qryExampleList")
    def qry_example_list(params: dict[str, str]) -> list[tuple]:
        rows = Record.objects.order_by("pk")
        return list(rows.values_list("pk", "name"))

formula_output, a workbook formula block, uses the same registry::

    @saved_query("Sheet1!E2:E20")
    def sheet1_totals(params: dict[str, str]) -> list[tuple]:
        return [(row.pk, row.total) for row in with_total(Record.objects.all())]

document, one rendered document per key: return its canonical fields
(docs/contracts.md#documents)::

    @document("example_pdf")
    def example_pdf(key: str, params: dict[str, str]) -> dict:
        record = Record.objects.get(pk=int(key))
        return {"number": str(record.pk), "name": record.name}
"""

from __slug__.core.saved_queries import document  # noqa: F401 (the examples use it)
from __slug__.core.saved_queries import saved_query  # noqa: F401 (the examples use it)
