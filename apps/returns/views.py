import django_filters as df
from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView

from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.payments.models import Payment
from apps.sales.models import Invoice

from . import services
from .models import SalesReturn, SalesReturnLine


class ReturnFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Return / invoice no.")
    status = df.ChoiceFilter(choices=SalesReturn.Status.choices)

    class Meta:
        model = SalesReturn
        fields = ["q", "status"]

    def search(self, qs, name, value):
        from django.db.models import Q
        return qs.filter(Q(number__icontains=value) | Q(invoice__number__icontains=value))


class ReturnListView(FilteredListView):
    permission_required = "returns.view_salesreturn"
    model = SalesReturn
    filterset_class = ReturnFilter
    title = "Returns & credit notes"
    columns = (("Return", "number"), ("Invoice", "invoice", "text", "nolink"), ("Date", "return_date", "date"),
               ("Reason", "reason", "text"), ("Status", "get_status_display", "status"),
               ("Credit", "credit_amount", "money"), ("Refunded", "refund_amount", "money"))

    def get_queryset(self):
        return super().get_queryset().select_related("invoice")


class ReturnCreateView(AppPermissionMixin, View):
    permission_required = "returns.add_salesreturn"

    def _invoice(self, request):
        return get_object_or_404(Invoice, pk=request.GET.get("invoice") or request.POST.get("invoice"))

    def get(self, request):
        inv = self._invoice(request)
        return render(request, "returns/form.html", {"invoice": inv, "rows": services.refundable_summary(inv),
                                                     "dispositions": SalesReturnLine.Disposition.choices})

    def post(self, request):
        inv = self._invoice(request)
        lines = []
        for il in inv.lines.all():
            raw = request.POST.get(f"qty_{il.pk}", "").strip()
            if raw and raw != "0":
                if not raw.isdigit():
                    messages.error(request, f"Invalid quantity for {il.description}.")
                    return redirect(request.get_full_path())
                lines.append({"invoice_line_id": il.pk, "quantity": int(raw),
                              "disposition": request.POST.get(f"disp_{il.pk}", "")})
        try:
            ret = services.request_return(invoice_id=inv.pk, lines=lines, reason=request.POST.get("reason", ""),
                                          user=request.user)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
            return render(request, "returns/form.html", {"invoice": inv, "rows": services.refundable_summary(inv),
                                                         "dispositions": SalesReturnLine.Disposition.choices,
                                                         "posted": request.POST})
        messages.success(request, f"Return {ret.number} submitted for approval.")
        return redirect(ret)


class ApproveForm(forms.Form):
    refund_method = forms.ChoiceField(choices=[("", "— no refund needed —")] + Payment.Method.choices, required=False)
    note = forms.CharField(max_length=255, required=False)


class ReturnDetailView(AppPermissionMixin, DetailView):
    permission_required = "returns.view_salesreturn"
    model = SalesReturn
    template_name = "returns/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.lines.select_related("invoice_line")
        ctx["approve_form"] = ApproveForm()
        inv = self.object.invoice
        ctx["refund_possible"] = inv.total_paid > 0
        return ctx


class ReturnDecisionView(AppPermissionMixin, View):
    permission_required = "returns.approve_return"

    def post(self, request, pk, decision):
        form = ApproveForm(request.POST)
        form.is_valid()
        d = getattr(form, "cleaned_data", {})
        try:
            if decision == "approve":
                services.approve_return(pk, user=request.user, refund_method=d.get("refund_method") or None,
                                        note=d.get("note", ""))
                messages.success(request, "Return approved: stock and balances updated.")
            else:
                services.reject_return(pk, user=request.user, note=d.get("note", "") or "Rejected")
                messages.info(request, "Return rejected; nothing changed.")
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        return redirect("returns:detail", pk=pk)
