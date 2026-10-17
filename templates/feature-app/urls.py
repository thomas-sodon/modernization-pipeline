"""URLs of the __area_title__ area, mounted at /__area__/ by the overlay.

The contract names one URL per kept form, form_<file-safe id>, and one per kept
report, report_<file-safe id> (docs/contracts.md#feature-app-contract). Rename the
example routes to those names, or add the contract names next to them, when the
screens take shape.
"""

from django.urls import path

from . import views

app_name = "__area__"

urlpatterns = [
    path("", views.RecordListView.as_view(), name="list"),
    path("new/", views.RecordCreateView.as_view(), name="create"),
    path("<int:pk>/", views.RecordDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.RecordUpdateView.as_view(), name="update"),
    path("<int:pk>/print/", views.RecordPrintView.as_view(), name="print"),
]
