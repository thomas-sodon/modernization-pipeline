"""django-tables2 tables of the __area_title__ area."""

import django_tables2 as tables

from .services import LIST_FIELDS
from .services import Record


class RecordTable(tables.Table):
    """The list screen's table; the number links to the detail screen."""

    number = tables.Column(
        accessor="pk",
        verbose_name="No.",
        linkify=("__area__:detail", {"pk": tables.A("pk")}),
    )

    class Meta:
        model = Record
        fields = ("number", *LIST_FIELDS)
        attrs = {"class": "table table-sm table-striped"}
