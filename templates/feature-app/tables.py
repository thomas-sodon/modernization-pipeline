"""django-tables2 tables of the __area_title__ area."""

import django_tables2 as tables

from .services import LIST_FIELDS
from .services import Record

#: hx-boost for the parts of a list table. list.html boosts its #record-table
#: container, so the sort links (thead) and the paging links swap only the table.
#: The rows and the footer switch the boost off: a record link opens its page as a
#: normal page load, never inside the list. Give every table of the area these.
UNBOOSTED_ROWS = {"tbody": {"hx-boost": "false"}, "tfoot": {"hx-boost": "false"}}


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
        attrs = {"class": "table table-sm table-striped", **UNBOOSTED_ROWS}
