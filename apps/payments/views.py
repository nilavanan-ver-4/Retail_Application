import django_filters as df
from django import forms
from django.contrib import messages
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from apps.core.csvutil import csv_response
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.sales.forms import PaymentForm
from apps.sales.models import Invoice

from . import services
from .models import Payment

date_widget = forms.DateInput(attrs={"type": "date"})


class PaymentFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Invoice / customer / receipt")
    method = df.ChoiceFilter(choices=Payment.Method.choices)
    kind = df.ChoiceFilter(choices=Payment.Kind.choices)
    status = df.ChoiceFilter(choices=Payment.Status.choices)
    date_from = df.DateFilter(field_name="paid_on", lookup_expr="gte", widget=date_widget)
    date_to = df.DateFilter(field_name="paid_on", lookup_expr="lte", widget=date_widget)

    class Meta:
        model = Payment
        fields = ["q", "method", "kind", "status", "date_from", "date_to"]

    def search(self, qs, name, value):
        from django.db.models import Q

        return qs.filter(
            Q(invoice__number__icontains=value)
            | Q(invoice__customer_name__icontains=value)
            | Q(receipt_no__icontains=value)
            | Q(reference__icontains=value)
        )


class PaymentListView(FilteredListView):
    permission_required = "payments.view_payment"
    model = Payment
    filterset_class = PaymentFilter
    template_name = "payments/list.html"
    title = "Payments"
    paginate_by = 50
    columns = (
        ("Date", "paid_on", "date", "nolink"),
        ("Receipt", "receipt_no", "text", "nolink"),
        ("Invoice", "invoice", "text", "nolink"),
        ("Customer", "invoice.customer_name", "text", "nolink"),
        ("Kind", "get_kind_display", "text"),
        ("Method", "get_method_display", "text"),
        ("Amount", "amount", "money"),
        ("Status", "get_status_display", "status"),
    )

    def get_queryset(self):
        qs = super().get_queryset().select_related("invoice")
        if not self.request.user.has_perm("payments.view_refund"):
            qs = qs.filter(kind=Payment.Kind.PAYMENT)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        done = self.filterset.qs.filter(status=Payment.Status.COMPLETED)
        ctx["received"] = done.filter(kind="payment").aggregate(s=Sum("amount"))["s"] or 0
        ctx["refunded"] = done.filter(kind="refund").aggregate(s=Sum("amount"))["s"] or 0
        ctx["by_method"] = (
            done.filter(kind="payment").values_list("method").annotate(s=Sum("amount")).order_by("-s")
        )
        return ctx

    def get_actions(self):
        return [
            {
                "label": "Export CSV",
                "url": reverse("payments:export") + "?" + self.request.GET.urlencode(),
                "kind": "",
            }
        ]


class PaymentExportView(AppPermissionMixin, View):
    permission_required = "payments.view_payment"

    def get(self, request):
        base = Payment.objects.select_related("invoice")
        if not request.user.has_perm("payments.view_refund"):
            base = base.filter(kind=Payment.Kind.PAYMENT)
        qs = PaymentFilter(request.GET or None, queryset=base).qs
        rows = [
            (
                p.paid_on,
                p.receipt_no,
                p.invoice.number,
                p.invoice.customer_name,
                p.kind,
                p.method,
                p.amount,
                p.status,
                p.reference,
            )
            for p in qs.iterator()
        ]
        return csv_response(
            "payments.csv",
            ["date", "receipt", "invoice", "customer", "kind", "method", "amount", "status", "reference"],
            rows,
        )


class PaymentCreateView(AppPermissionMixin, View):
    permission_required = "payments.add_payment"

    def post(self, request, invoice_id):
        invoice = get_object_or_404(Invoice, pk=invoice_id)
        form = PaymentForm(request.POST)
        if not form.is_valid():
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
            return redirect(invoice)
        d = form.cleaned_data
        try:
            payment, created = services.record_payment(
                invoice.pk,
                amount=d["amount"],
                method=d["method"],
                user=request.user,
                paid_on=d["paid_on"],
                reference=d["reference"],
                idempotency_key=d["token"] or None,
            )
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Payment {payment.receipt_no} recorded."
                if created
                else "That payment was already recorded; nothing changed.",
            )
        return redirect(invoice)


class ReverseForm(forms.Form):
    reason = forms.CharField(max_length=255)


class PaymentReverseView(AppPermissionMixin, View):
    permission_required = "payments.reverse_payment"

    def _payment(self, pk):
        return get_object_or_404(Payment.objects.select_related("invoice"), pk=pk)

    def post(self, request, pk):
        payment = self._payment(pk)
        if "reason" not in request.POST:
            return render(
                request,
                "generic/confirm.html",
                {
                    "title": f"Reverse payment {payment.receipt_no or payment.pk}",
                    "form": ReverseForm(),
                    "message": f"Reverse {payment.amount} received on invoice {payment.invoice.number}? "
                    "The invoice balance will be re-opened.",
                    "cancel_url": payment.invoice.get_absolute_url(),
                    "submit_label": "Reverse payment",
                },
            )
        form = ReverseForm(request.POST)
        if form.is_valid():
            try:
                services.reverse_payment(pk, user=request.user, reason=form.cleaned_data["reason"])
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Payment reversed.")
        else:
            messages.error(request, "A reason is required.")
        return redirect(payment.invoice)
