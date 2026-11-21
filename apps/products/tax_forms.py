from decimal import Decimal

from django import forms

from .models import Category, Product, TaxConfiguration


class TaxConfigurationForm(forms.ModelForm):
    class Meta:
        model = TaxConfiguration
        fields = ["label", "registration_number", "default_rate"]


class ProductTaxForm(forms.Form):
    products = forms.ModelMultipleChoiceField(queryset=Product.objects.all())
    rate = forms.DecimalField(
        min_value=Decimal("0"), max_value=Decimal("100"), max_digits=5, decimal_places=2
    )
    reason = forms.CharField(max_length=255, help_text="Recorded in the audit log.")


class TaxSummaryFilterForm(forms.Form):
    start = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("start") and data.get("end") and data["start"] > data["end"]:
            raise forms.ValidationError("Start date must be on or before end date.")
        return data


class TaxProductFilterForm(forms.Form):
    q = forms.CharField(required=False, label="Search name or SKU")
    category = forms.ModelChoiceField(queryset=Category.objects.all(), required=False)
