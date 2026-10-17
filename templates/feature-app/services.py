"""Services of the __area_title__ area: one function per services[] entry of
analysis/interfaces.md for this area.

Views call a service for every write. A service checks its rules first; a refused
action raises ValidationError with a plain message that names the rule id and
changes nothing. Calculations that other areas share come from
app/__slug__/domain/ (its README lists them); never copy one here. When the
engagement stamps audit columns, wrap each write in the stamp context the domain
README names.

`Record` and the field lists below drive the example screens. Set them to the area's
main table before apps.py goes in (README.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

if TYPE_CHECKING:
    from django.db.models import Model

#: The legacy model the example screens show and edit, e.g. legacy.models.<Model>.
Record: type[Model] | None = None
#: Columns of the list table, editable fields of the form, fields of the filter.
LIST_FIELDS: tuple[str, ...] = ()
FORM_FIELDS: tuple[str, ...] = ()
FILTER_FIELDS: tuple[str, ...] = ()
#: Fields the example rule requires.
REQUIRED_FIELDS: tuple[str, ...] = ()
#: The rule the example service enforces: put a rule id of this area here.
EXAMPLE_RULE = "R-__area__-000"


def save_record(record: Model, data: dict[str, Any]) -> Model:
    """Create or update one record from cleaned form data.

    EXAMPLE_RULE: every field of REQUIRED_FIELDS is filled in. A refused save raises
    ValidationError naming the rule id and writes nothing.
    """
    missing = [name for name in REQUIRED_FIELDS if data.get(name) in (None, "")]
    if missing:
        msg = f"{EXAMPLE_RULE}: fill in {', '.join(missing)}."
        raise ValidationError(msg, code=EXAMPLE_RULE)
    with transaction.atomic():
        for name, value in data.items():
            setattr(record, name, value)
        record.full_clean()
        record.save()
    return record
