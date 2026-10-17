"""Forms of the __area_title__ area (crispy-forms, Bootstrap 5)."""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django import forms

from .services import FORM_FIELDS
from .services import Record


class RecordForm(forms.ModelForm):
    """The create and edit form; saving goes through services.save_record."""

    class Meta:
        model = Record
        fields = FORM_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Submit("save", "Save"))
