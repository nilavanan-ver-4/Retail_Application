from django import forms

from .models import Supplier


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "company", "mobile", "email", "address", "terms", "is_active"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "terms": forms.Textarea(attrs={"rows": 2})}
