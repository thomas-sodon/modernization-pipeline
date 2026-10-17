"""Screens of the __area_title__ area.

Every view signs in first (anonymous users go to the sign-in page) and then checks
the role: the roles of core/local/roles.py are Django groups with permissions
(core.roles keeps them in step), so a user passes when one of their groups grants
the view's permission and gets 403 otherwise. Writes go through services.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.views.generic import CreateView
from django.views.generic import DetailView
from django.views.generic import UpdateView
from django_filters.views import FilterView
from django_tables2 import SingleTableMixin
from django_weasyprint.views import WeasyTemplateResponse

from . import services
from .filters import RecordFilter
from .forms import RecordForm
from .services import Record
from .tables import RecordTable


class AreaAccessMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Sign-in, then the role check on `<app_label>.<action>_<model>`."""

    action = "view"

    def get_permission_required(self):
        opts = self.model._meta  # noqa: SLF001 (Django's documented Options API)
        return (f"{opts.app_label}.{self.action}_{opts.model_name}",)


class RecordListView(AreaAccessMixin, SingleTableMixin, FilterView):
    """List with filters; an HTMX request gets only the table partial."""

    model = Record
    table_class = RecordTable
    filterset_class = RecordFilter
    template_name = "__area__/list.html"
    table_pagination = {"per_page": 25}

    def get_queryset(self):
        return super().get_queryset().order_by("pk")

    def get_template_names(self):
        if self.request.htmx:
            return [f"{self.template_name}#record-table"]
        return [self.template_name]


class RecordDetailView(AreaAccessMixin, DetailView):
    model = Record
    template_name = "__area__/detail.html"


class ServiceFormMixin:
    """Save through services.save_record; a refused save shows its message."""

    def form_valid(self, form):
        try:
            self.object = services.save_record(form.instance, form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("__area__:detail", kwargs={"pk": self.object.pk})


class RecordCreateView(AreaAccessMixin, ServiceFormMixin, CreateView):
    action = "add"
    model = Record
    form_class = RecordForm
    template_name = "__area__/form.html"


class RecordUpdateView(AreaAccessMixin, ServiceFormMixin, UpdateView):
    action = "change"
    model = Record
    form_class = RecordForm
    template_name = "__area__/form.html"


class RecordPrintView(AreaAccessMixin, DetailView):
    """The print view; ?format=pdf returns it as an A4 PDF (django-weasyprint)."""

    model = Record
    template_name = "__area__/print.html"

    def render_to_response(self, context, **response_kwargs):
        if self.request.GET.get("format") == "pdf":
            return WeasyTemplateResponse(
                request=self.request,
                template=self.get_template_names(),
                context=context,
                filename=f"__area__-{self.object.pk}.pdf",
                attachment=False,
            )
        return super().render_to_response(context, **response_kwargs)
