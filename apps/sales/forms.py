import django_filters as df
from django import forms
from django.db.models import Q

from apps.customers.models import Customer
from apps.payments.models import Payment

from .models import Invoice

date_widget = forms.DateInput(attrs={"type": "date"})


class InvoiceFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search (no., customer, mobile)")
    customer = df.ModelChoiceFilter(queryset=Customer.objects.all())
    status = df.ChoiceFilter(choices=Invoice.Status.choices)
    payment_status = df.ChoiceFilter(choices=Invoice.PaymentStatus.choices)
    date_from = df.DateFilter(field_name="date", lookup_expr="gte", widget=date_widget)
    date_to = df.DateFilter(field_name="date", lookup_expr="lte", widget=date_widget)
    product = df.CharFilter(method="by_product", label="Contains product (name/SKU)")

    class Meta:
        model = Invoice
        fields = ["q", "customer", "status", "payment_status", "date_from", "date_to", "product"]

    def search(self, qs, name, value):
        return qs.filter(
            Q(number__icontains=value)
            | Q(customer_name__icontains=value)
            | Q(customer_mobile__icontains=value)
        )

    def by_product(self, qs, name, value):
        return qs.filter(
            Q(lines__description__icontains=value) | Q(lines__product__sku__icontains=value)
        ).distinct()


class CancelForm(forms.Form):
    reason = forms.CharField(max_length=255, label="Reason for cancelling")


class PaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=0.01, max_digits=12, decimal_places=2)
    method = forms.ChoiceField(choices=Payment.Method.choices)
    paid_on = forms.DateField(widget=date_widget)
    reference = forms.CharField(
        max_length=100,
        required=False,
        help_text="Terminal slip / bank reference / UPI transaction id. Never enter card numbers.",
    )
    token = forms.CharField(widget=forms.HiddenInput, required=False)
