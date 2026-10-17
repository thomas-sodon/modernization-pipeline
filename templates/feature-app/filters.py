"""django-filter filter sets of the __area_title__ area."""

import django_filters

from .services import FILTER_FIELDS
from .services import Record


class RecordFilter(django_filters.FilterSet):
    """The list screen's filters (the original form's filter options)."""

    class Meta:
        model = Record
        fields = FILTER_FIELDS
