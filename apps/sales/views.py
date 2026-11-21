import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView

from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.core.words import amount_in_words
from apps.customers.models import Customer
from apps.payments.models import Payment
from apps.products.models import Product

from . import pdf
from .forms import CancelForm, InvoiceFilter, PaymentForm
from .models import Invoice
from .services import cancel_invoice, create_invoice


class InvoiceListView(FilteredListView):
    permission_required = "sales.view_invoice"
    model = Invoice
    filterset_class = InvoiceFilter
    title = "Invoices"
    columns = (
        ("Invoice", "number"),
        ("Date", "date", "date"),
        ("Customer", "customer_name", "text", "nolink"),
        ("Mobile", "customer_mobile", "text", "nolink"),
        ("Total", "total", "money"),
        ("Paid", "total_paid", "money"),
        ("Status", "status", "status"),
        ("Payment", "payment_status", "status"),
        ("Sold by", "sold_by", "text", "nolink"),
    )
    actions = (("New sale", "sales:create", "sales.add_invoice", "primary"),)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from django.db.models import Sum

        qs = (
            self.filterset.qs.exclude(status=Invoice.Status.CANCELLED)
            if self.filterset
            else Invoice.objects.none()
        )
        ctx["totals"] = qs.aggregate(total=Sum("total"), paid=Sum("total_paid"))
        return ctx

    template_name = "sales/list.html"


class InvoiceDetailView(AppPermissionMixin, DetailView):
    permission_required = "sales.view_invoice"
    model = Invoice
    template_name = "sales/detail.html"

    def get_queryset(self):
        return Invoice.objects.select_related("customer", "created_by", "cancelled_by").prefetch_related(
            "lines", Prefetch("payments", queryset=Payment.objects.order_by("paid_on", "id")), "returns"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        inv = self.object
        ctx["payment_form"] = PaymentForm(
            initial={
                "amount": inv.balance_due if inv.balance_due > 0 else None,
                "paid_on": timezone.localdate(),
                "token": uuid.uuid4().hex,
            }
        )
        ctx["cancel_form"] = CancelForm()
        ctx["can_pay"] = (
            inv.status != Invoice.Status.CANCELLED
            and inv.balance_due > 0
            and self.request.user.has_perm("payments.add_payment")
        )
        return ctx


def _print_context(invoice):
    return {
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": invoice.payments.filter(status=Payment.Status.COMPLETED),
        "in_words": amount_in_words(invoice.total),
    }


class InvoicePrintView(AppPermissionMixin, View):
    permission_required = "sales.view_invoice"

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
        return render(request, "sales/invoice_print.html", {**_print_context(invoice), "logo_uri": ""})


class InvoicePdfView(AppPermissionMixin, View):
    permission_required = "sales.view_invoice"

    def get(self, request, pk):
        invoice = get_object_or_404(Invoice.objects.select_related("customer"), pk=pk)
        data = pdf.render_pdf("sales/invoice_print.html", _print_context(invoice))
        resp = HttpResponse(data, content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="invoice-{invoice.number}.pdf"'
        return resp


class InvoiceCancelView(AppPermissionMixin, View):
    permission_required = "sales.cancel_invoice"

    def post(self, request, pk):
        form = CancelForm(request.POST)
        if not form.is_valid():
            messages.error(request, "A reason is required to cancel an invoice.")
            return redirect("sales:detail", pk=pk)
        try:
            cancel_invoice(pk, user=request.user, reason=form.cleaned_data["reason"])
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Invoice cancelled; stock restored.")
        return redirect("sales:detail", pk=pk)


# ----------------------------------------------------------------------------
# Billing screen
# ----------------------------------------------------------------------------
def _dec(raw, label, default=Decimal("0")):
    raw = (raw or "").strip()
    if raw == "":
        return default
    try:
        d = Decimal(raw)
    except InvalidOperation:
        raise BusinessRuleError(f"{label} is not a valid number.") from None
    if not d.is_finite():
        raise BusinessRuleError(f"{label} is not a valid number.")
    return d


class InvoiceCreateView(AppPermissionMixin, View):
    permission_required = "sales.add_invoice"
    template_name = "sales/create.html"

    def _context(self, request, posted=None, error=None, token=None):
        user = request.user
        initial_lines = posted["lines"] if posted else []
        ctx = {
            "token": token or uuid.uuid4().hex,
            "today": timezone.localdate().isoformat(),
            "can_override_price": user.has_perm("sales.override_price"),
            "methods": Payment.Method.choices,
            "posted": posted or {},
            "error": error,
            "init_json": {
                "lines": initial_lines,
                "payments": (posted or {}).get("payments", []),
                "canOverride": user.has_perm("sales.override_price"),
            },
        }
        p = posted or {}
        pre = None
        if request.GET.get("customer") and not posted:
            pre = Customer.objects.filter(pk=request.GET["customer"], is_active=True).first()
        ctx["f"] = {
            "mobile": p.get("mobile") or (pre.mobile if pre else ""),
            "name": p.get("name") or (pre.name if pre else ""),
            "address": p.get("address") or (pre.billing_address if pre else ""),
            "email": p.get("email") or (pre.email if pre else ""),
            "date": p.get("date") or timezone.localdate().isoformat(),
            "sold_by": p.get("sold_by") or user.display_name(),
            "terms": p.get("terms", ""),
            "discount_percent": p.get("discount_percent", "0"),
            "shipping": p.get("shipping", "0"),
        }
        return ctx

    def get(self, request):
        ctx = self._context(request)
        reissue = request.GET.get("from")
        if reissue:
            src = get_object_or_404(Invoice, pk=reissue)
            lines = [
                {
                    "id": ln.product_id,
                    "name": ln.description,
                    "price": str(ln.product.selling_price),
                    "quantity": ln.quantity,
                    "discount": str(ln.discount),
                    "stock": ln.product.stock_on_hand,
                    "tax_rate": str(ln.product.tax_rate),
                }
                for ln in src.lines.select_related("product")
            ]
            ctx["f"].update(
                {
                    "mobile": src.customer_mobile,
                    "name": src.customer_name,
                    "address": src.billing_address,
                    "terms": src.terms,
                }
            )
            ctx["init_json"] = {"lines": lines, "payments": [], "canOverride": ctx["can_override_price"]}
        return render(request, self.template_name, ctx)

    def post(self, request):
        user = request.user
        P = request.POST
        token = P.get("token", "")[:64]
        posted = {
            "mobile": P.get("mobile", "").strip(),
            "name": P.get("name", "").strip(),
            "address": P.get("address", ""),
            "email": P.get("email", ""),
            "terms": P.get("terms", ""),
            "sold_by": P.get("sold_by", ""),
            "date": P.get("date", ""),
            "discount_percent": P.get("discount_percent", "0"),
            "shipping": P.get("shipping_charge", "0"),
            "lines": [],
            "payments": [],
        }
        try:
            ids = P.getlist("product_id")
            qtys, discs, prices = P.getlist("quantity"), P.getlist("discount"), P.getlist("unit_price")
            lines = []
            can_override = user.has_perm("sales.override_price")
            for i, pid in enumerate(ids):
                if not pid.isdigit():
                    raise BusinessRuleError("Invalid product on the invoice.")
                qty_raw = qtys[i] if i < len(qtys) else ""
                qty = _dec(qty_raw, f"Line {i + 1} quantity")
                if qty != qty.to_integral_value() or qty <= 0:
                    raise BusinessRuleError(f"Line {i + 1}: quantity must be a positive whole number.")
                line = {
                    "product_id": int(pid),
                    "quantity": int(qty),
                    "discount": _dec(discs[i] if i < len(discs) else "", f"Line {i + 1} discount"),
                }
                if can_override and i < len(prices) and prices[i].strip():
                    line["unit_price"] = _dec(prices[i], f"Line {i + 1} price")
                lines.append(line)
                prod = Product.objects.filter(pk=int(pid)).first()
                posted["lines"].append(
                    {
                        "id": int(pid),
                        "name": prod.name if prod else "?",
                        "quantity": int(qty),
                        "price": str(line.get("unit_price", prod.selling_price if prod else 0)),
                        "discount": str(line["discount"]),
                        "stock": prod.stock_on_hand if prod else 0,
                        "tax_rate": str(prod.tax_rate) if prod else "0",
                    }
                )
            payments = []
            for m, a, r in zip(
                P.getlist("pay_method"), P.getlist("pay_amount"), P.getlist("pay_reference"), strict=False
            ):
                if a.strip() == "":
                    continue
                payments.append({"method": m, "amount": _dec(a, "Payment amount"), "reference": r})
                posted["payments"].append({"method": m, "amount": a, "reference": r})
            date_raw = P.get("date") or ""
            invoice, created = create_invoice(
                user=user,
                lines=lines,
                customer_mobile=posted["mobile"],
                customer_name=posted["name"],
                customer_address=posted["address"],
                customer_email=posted["email"],
                terms=posted["terms"],
                sold_by=posted["sold_by"],
                invoice_date=date.fromisoformat(date_raw) if date_raw else None,
                discount_percent=_dec(P.get("discount_percent"), "Discount %"),
                shipping_charge=_dec(P.get("shipping_charge"), "Shipping"),
                payments=payments,
                submission_token=token or None,
                can_override_price=can_override,
                can_override_credit=user.has_perm("customers.override_credit_limit"),
            )
        except (BusinessRuleError, ValueError) as exc:
            msg = (
                str(exc)
                if isinstance(exc, BusinessRuleError)
                else "Please check the date and numbers entered."
            )
            return render(request, self.template_name, self._context(request, posted, msg, token))
        if created:
            messages.success(request, f"Invoice {invoice.number} created.")
        else:
            messages.info(request, f"Invoice {invoice.number} was already created from this form.")
        return redirect("sales:detail", pk=invoice.pk)
