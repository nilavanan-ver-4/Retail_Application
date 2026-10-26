from decimal import Decimal

from django import forms

from apps.products.models import Product
from apps.suppliers.models import Supplier

from .models import PurchaseOrder, SupplierInvoice, SupplierPayment

date_widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class OrderForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.filter(is_active=True))
    order_date = forms.DateField(widget=date_widget)
    expected_date = forms.DateField(widget=date_widget, required=False)
    discount = forms.DecimalField(min_value=Decimal("0"), decimal_places=2, max_digits=12,
                                  initial=Decimal("0.00"), required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def clean_discount(self):
        return self.cleaned_data.get("discount") or Decimal("0.00")


class LineForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True), required=False)
    quantity = forms.IntegerField(min_value=1, required=False)
    unit_cost = forms.DecimalField(min_value=Decimal("0"), decimal_places=2, max_digits=12, required=False,
                                   help_text="Blank = product's current cost")
    discount = forms.DecimalField(min_value=Decimal("0"), decimal_places=2, max_digits=12, required=False)

    def clean(self):
        d = super().clean()
        if d.get("product") and not d.get("quantity"):
            self.add_error("quantity", "Enter a quantity.")
        if d.get("quantity") and not d.get("product"):
            self.add_error("product", "Choose a product.")
        return d


class ReceiptHeaderForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.filter(is_active=True))
    receipt_date = forms.DateField(widget=date_widget)
    delivery_note = forms.CharField(max_length=100, required=False)
    comments = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)


class SupplierInvoiceForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.filter(is_active=True))
    invoice_number = forms.CharField(max_length=50, required=False,
                                     help_text="Supplier's own bill number. Blank = auto-generate.")
    invoice_date = forms.DateField(widget=date_widget)
    due_date = forms.DateField(widget=date_widget, required=False)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), required=False)

    def clean_invoice_number(self):
        n = self.cleaned_data["invoice_number"].strip()
        if n and SupplierInvoice.objects.filter(invoice_number=n).exists():
            raise forms.ValidationError("A supplier invoice with this number already exists.")
        return n


class SupplierPaymentForm(forms.ModelForm):
    token = forms.CharField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = SupplierPayment
        fields = ["amount", "method", "paid_on", "reference"]
        widgets = {"paid_on": date_widget}


class QuickReceiveForm(forms.Form):
    quantity = forms.IntegerField(min_value=1)
    unit_cost = forms.DecimalField(min_value=Decimal("0"), decimal_places=2, max_digits=12, required=False,
                                   help_text="Blank = product's current cost")
    comments = forms.CharField(max_length=255, required=False)
