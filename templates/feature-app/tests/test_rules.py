"""Rule tests of the __area_title__ area.

One test per rule of this area whose test_kind is rule (analysis/rules.json), with
the rule id first in the docstring, data from legacy/factories.py and the expected
values worked out by hand in the test. Never golden files or seed outputs.
"""

import pytest
from django.core.exceptions import ValidationError

from __slug__.legacy.factories import FACTORIES

# isort: split
# (the area's own modules sort after the shared ones, whatever the area id)
from __slug__.__area__ import services

pytestmark = pytest.mark.django_db


def test_example_rule_refuses_a_blank_required_field(monkeypatch):
    """R-__area__-000: a record with a required field left blank is not saved; the
    refusal names the rule and nothing changes."""
    record = FACTORIES[services.Record].create()
    field = services.FORM_FIELDS[0]
    monkeypatch.setattr(services, "REQUIRED_FIELDS", (field,))
    stored = services.Record.objects.filter(pk=record.pk).values(field).get()
    with pytest.raises(ValidationError, match=services.EXAMPLE_RULE):
        services.save_record(record, {field: ""})
    assert services.Record.objects.filter(pk=record.pk).values(field).get() == stored
