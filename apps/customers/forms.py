from decimal import Decimal

from django import forms

from .models import Customer, LedgerEntry


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "name",
            "mobile",
            "email",
            "billing_address",
            "shipping_address",
            "credit_limit",
            "notes",
            "is_active",
        ]
        widgets = {
            "billing_address": forms.Textarea(attrs={"rows": 2}),
            "shipping_address": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def clean_mobile(self):
        mobile = self.cleaned_data["mobile"].strip().replace(" ", "")
        if Customer.objects.filter(mobile=mobile).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("A customer with this mobile number already exists.")
        return mobile


class ManualLedgerForm(forms.Form):
    customer = forms.ModelChoiceField(queryset=Customer.objects.filter(is_active=True))
    entry_type = forms.ChoiceField(
        choices=LedgerEntry.Type.choices, help_text="Debit = customer owes more; Credit = customer owes less."
    )
    amount = forms.DecimalField(min_value=Decimal("0.01"), max_digits=12, decimal_places=2)
    description = forms.CharField(max_length=255, help_text="Reason for the manual adjustment (audited).")
