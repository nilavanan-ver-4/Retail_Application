import uuid
from datetime import date

import django_filters as df
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.forms import formset_factory
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView

from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.products.models import Product
from apps.suppliers.models import Supplier

from . import services
from .forms import (LineForm, OrderForm, QuickReceiveForm, ReceiptHeaderForm, SupplierInvoiceForm,
                    SupplierPaymentForm)
from .models import GoodsReceipt, PurchaseOrder, SupplierInvoice


def _lines_from(formset):
    lines = []
    for f in formset:
        d = f.cleaned_data
        if d.get("product"):
            lines.append({"product": d["product"], "quantity": d["quantity"],
                          "unit_cost": d["unit_cost"] if d.get("unit_cost") is not None else d["product"].cost_price,
                          "discount": d.get("discount") or 0})
    return lines


LineFormSet = formset_factory(LineForm, extra=6)


# ------------------------------------------------------------ purchase orders
class OrderFilter(df.FilterSet):
    q = df.CharFilter(field_name="number", lookup_expr="icontains", label="Number")
    status = df.ChoiceFilter(choices=PurchaseOrder.Status.choices)
    supplier = df.ModelChoiceFilter(queryset=Supplier.objects.all())

    class Meta:
        model = PurchaseOrder
        fields = ["q", "status", "supplier"]


class OrderListView(FilteredListView):
    permission_required = "purchasing.view_purchaseorder"
    model = PurchaseOrder
    filterset_class = OrderFilter
    title = "Purchase orders"
    columns = (("Number", "number"), ("Supplier", "supplier", "text", "nolink"), ("Order date", "order_date", "date"),
               ("Expected", "expected_date", "date"), ("Status", "get_status_display", "status"),
               ("Total", "total", "money"))
    actions = (("New purchase order", "purchasing:order_create", "purchasing.add_purchaseorder", "primary"),)

    def get_queryset(self):
        return super().get_queryset().select_related("supplier").prefetch_related("lines")


class OrderCreateView(AppPermissionMixin, View):
    permission_required = "purchasing.add_purchaseorder"

    def _render(self, request, form, formset):
        return render(request, "purchasing/doc_form.html", {
            "title": "New purchase order", "form": form, "formset": formset,
            "cancel_url": reverse("purchasing:order_list")})

    def get(self, request):
        initial = {"order_date": timezone.localdate(), "supplier": request.GET.get("supplier")}
        return self._render(request, OrderForm(initial=initial), LineFormSet())

    def post(self, request):
        form, formset = OrderForm(request.POST), LineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = _lines_from(formset)
            try:
                status = (PurchaseOrder.Status.ORDERED if "place" in request.POST
                          else PurchaseOrder.Status.DRAFT)
                po = services.create_purchase_order(
                    supplier=form.cleaned_data["supplier"], order_date=form.cleaned_data["order_date"],
                    expected_date=form.cleaned_data["expected_date"], notes=form.cleaned_data["notes"],
                    discount=form.cleaned_data["discount"], user=request.user, status=status,
                    lines=[{"product": ln["product"], "quantity": ln["quantity"], "unit_cost": ln["unit_cost"],
                            "discount": ln["discount"], "tax_rate": ln["product"].tax_rate} for ln in lines])
            except BusinessRuleError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Purchase order {po.number} created.")
                return redirect(po)
        return self._render(request, form, formset)


class OrderDetailView(AppPermissionMixin, DetailView):
    permission_required = "purchasing.view_purchaseorder"
    model = PurchaseOrder
    template_name = "purchasing/order_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.lines.select_related("product")
        ctx["receipts"] = self.object.receipts.all()
        return ctx


class OrderStatusView(AppPermissionMixin, View):
    permission_required = "purchasing.change_purchaseorder"

    def post(self, request, pk, action):
        with transaction.atomic():
            po = PurchaseOrder.objects.select_for_update().get(pk=pk)
            if action == "place" and po.status == PurchaseOrder.Status.DRAFT:
                po.status = PurchaseOrder.Status.ORDERED
            elif action == "cancel" and po.status in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.ORDERED) \
                    and not po.receipts.filter(status=GoodsReceipt.Status.CONFIRMED).exists():
                po.status = PurchaseOrder.Status.CANCELLED
            else:
                messages.error(request, "That change is not allowed for this order's current state.")
                return redirect(po)
            po.save(update_fields=["status", "updated_at"])
        messages.success(request, f"Order {po.number} is now {po.get_status_display().lower()}.")
        return redirect(po)


# ------------------------------------------------------------ goods receipts
class ReceiptFilter(df.FilterSet):
    q = df.CharFilter(field_name="number", lookup_expr="icontains", label="Number")
    status = df.ChoiceFilter(choices=GoodsReceipt.Status.choices)

    class Meta:
        model = GoodsReceipt
        fields = ["q", "status"]


class ReceiptListView(FilteredListView):
    permission_required = "purchasing.view_goodsreceipt"
    model = GoodsReceipt
    filterset_class = ReceiptFilter
    title = "Goods receipts"
    columns = (("Number", "number"), ("Supplier", "supplier", "text", "nolink"), ("Date", "receipt_date", "date"),
               ("PO", "purchase_order", "text", "nolink"), ("Status", "get_status_display", "status"))
    actions = (("New receipt", "purchasing:receipt_create", "purchasing.add_goodsreceipt", "primary"),)

    def get_queryset(self):
        return super().get_queryset().select_related("supplier", "purchase_order")


class ReceiptCreateView(AppPermissionMixin, View):
    permission_required = "purchasing.add_goodsreceipt"

    def _po(self, request):
        pk = request.GET.get("po") or request.POST.get("po")
        return get_object_or_404(PurchaseOrder, pk=pk) if pk else None

    def _render(self, request, form, formset, po):
        return render(request, "purchasing/doc_form.html", {
            "title": f"Receive goods{' for ' + po.number if po else ''}", "form": form, "formset": formset,
            "po": po, "cancel_url": reverse("purchasing:receipt_list"), "receipt": True})

    def get(self, request):
        po = self._po(request)
        initial = {"receipt_date": timezone.localdate()}
        lines = []
        if po:
            initial["supplier"] = po.supplier_id
            lines = [{"product": ln.product_id, "quantity": ln.outstanding, "unit_cost": ln.unit_cost}
                     for ln in po.lines.all() if ln.outstanding > 0]
        fs = formset_factory(LineForm, extra=0 if lines else 6)(initial=lines)
        return self._render(request, ReceiptHeaderForm(initial=initial), fs, po)

    def post(self, request):
        po = self._po(request)
        form, formset = ReceiptHeaderForm(request.POST), LineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = _lines_from(formset)
            if po:  # tie each line to its PO line
                by_product = {ln.product_id: ln for ln in po.lines.all()}
                for ln in lines:
                    pol = by_product.get(ln["product"].pk)
                    if pol is None:
                        form.add_error(None, f"{ln['product'].name} is not on {po.number}.")
                    ln["po_line"] = pol
            if not form.errors:
                try:
                    r = services.create_receipt(
                        supplier=form.cleaned_data["supplier"], receipt_date=form.cleaned_data["receipt_date"],
                        delivery_note=form.cleaned_data["delivery_note"], comments=form.cleaned_data["comments"],
                        purchase_order=po, user=request.user,
                        lines=[{"product": ln["product"], "quantity": ln["quantity"], "unit_cost": ln["unit_cost"],
                                "po_line": ln.get("po_line")} for ln in lines])
                except BusinessRuleError as exc:
                    form.add_error(None, str(exc))
                else:
                    messages.success(request, f"Receipt {r.number} saved as draft. Confirm it to add stock.")
                    return redirect(r)
        return self._render(request, form, formset, po)


class ReceiptDetailView(AppPermissionMixin, DetailView):
    permission_required = "purchasing.view_goodsreceipt"
    model = GoodsReceipt
    template_name = "purchasing/receipt_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.lines.select_related("product")
        ctx["invoices"] = self.object.invoices.all()
        return ctx


class ReceiptConfirmView(AppPermissionMixin, View):
    permission_required = "purchasing.receive_goods"

    def post(self, request, pk):
        try:
            r = services.confirm_receipt(pk, request.user)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
            return redirect("purchasing:receipt_detail", pk=pk)
        messages.success(request, f"Receipt {r.number} confirmed; stock updated.")
        return redirect(r)


class QuickReceiveView(AppPermissionMixin, View):
    """Legacy ``/add_item``: add stock for one product and book the supplier invoice."""
    permission_required = "purchasing.receive_goods"

    def get(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        return render(request, "generic/form.html", {
            "form": QuickReceiveForm(initial={"unit_cost": product.cost_price}),
            "title": f"Receive stock: {product.name}", "cancel_url": product.get_absolute_url(),
            "submit_label": "Receive & record supplier invoice"})

    def post(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        form = QuickReceiveForm(request.POST)
        if form.is_valid():
            try:
                receipt, inv = services.quick_receive(
                    product=product, quantity=form.cleaned_data["quantity"], user=request.user,
                    unit_cost=form.cleaned_data["unit_cost"], comments=form.cleaned_data["comments"])
            except BusinessRuleError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Received {form.cleaned_data['quantity']} x {product.name}; "
                                          f"supplier invoice {inv.invoice_number} recorded as unpaid.")
                return redirect("inventory:stock")
        return render(request, "generic/form.html", {
            "form": form, "title": f"Receive stock: {product.name}", "cancel_url": product.get_absolute_url()})


# ------------------------------------------------------------ supplier invoices
class SupplierInvoiceFilter(df.FilterSet):
    q = df.CharFilter(field_name="invoice_number", lookup_expr="icontains", label="Number")
    supplier = df.ModelChoiceFilter(queryset=Supplier.objects.all())
    payment_status = df.ChoiceFilter(choices=SupplierInvoice.PaymentStatus.choices)

    class Meta:
        model = SupplierInvoice
        fields = ["q", "supplier", "payment_status"]


class SupplierInvoiceListView(FilteredListView):
    permission_required = "purchasing.view_supplierinvoice"
    model = SupplierInvoice
    filterset_class = SupplierInvoiceFilter
    title = "Supplier invoices"
    columns = (("Invoice no.", "invoice_number"), ("Supplier", "supplier", "text", "nolink"),
               ("Date", "invoice_date", "date"), ("Total", "total_amount", "money"),
               ("Paid", "paid_amount", "money"), ("Payment", "payment_status", "status"))
    actions = (("Record supplier invoice", "purchasing:invoice_create", "purchasing.add_supplierinvoice", "primary"),)

    def get_queryset(self):
        return super().get_queryset().select_related("supplier")


class SupplierInvoiceCreateView(AppPermissionMixin, View):
    permission_required = "purchasing.add_supplierinvoice"

    def _render(self, request, form, formset):
        return render(request, "purchasing/doc_form.html", {
            "title": "Record supplier invoice", "form": form, "formset": formset,
            "cancel_url": reverse("purchasing:invoice_list")})

    def get(self, request):
        initial = {"invoice_date": date.today()}
        receipt = None
        lines = []
        if request.GET.get("receipt"):
            receipt = get_object_or_404(GoodsReceipt, pk=request.GET["receipt"])
            initial["supplier"] = receipt.supplier_id
            lines = [{"product": ln.product_id, "quantity": ln.quantity, "unit_cost": ln.unit_cost}
                     for ln in receipt.lines.all()]
        fs = formset_factory(LineForm, extra=0 if lines else 6)(initial=lines)
        return self._render(request, SupplierInvoiceForm(initial=initial), fs)

    def post(self, request):
        form, formset = SupplierInvoiceForm(request.POST), LineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = _lines_from(formset)
            receipt = GoodsReceipt.objects.filter(pk=request.GET.get("receipt")).first()
            try:
                inv = services.record_supplier_invoice(
                    supplier=form.cleaned_data["supplier"], invoice_number=form.cleaned_data["invoice_number"],
                    invoice_date=form.cleaned_data["invoice_date"], due_date=form.cleaned_data["due_date"],
                    notes=form.cleaned_data["notes"], user=request.user, receipt=receipt,
                    lines=[{"product": ln["product"], "quantity": ln["quantity"], "unit_cost": ln["unit_cost"]}
                           for ln in lines])
            except BusinessRuleError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Supplier invoice {inv.invoice_number} recorded (unpaid).")
                return redirect(inv)
        return self._render(request, form, formset)


class SupplierInvoiceDetailView(AppPermissionMixin, DetailView):
    permission_required = "purchasing.view_supplierinvoice"
    model = SupplierInvoice
    template_name = "purchasing/invoice_detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.lines.select_related("product")
        ctx["payments"] = self.object.payments.all()
        if self.request.user.has_perm("purchasing.add_supplierpayment") and self.object.balance > 0:
            ctx["pay_form"] = SupplierPaymentForm(initial={
                "amount": self.object.balance, "paid_on": timezone.localdate(), "token": uuid.uuid4().hex})
        return ctx


class SupplierPaymentView(AppPermissionMixin, View):
    permission_required = "purchasing.add_supplierpayment"

    def post(self, request, pk):
        inv = get_object_or_404(SupplierInvoice, pk=pk)
        form = SupplierPaymentForm(request.POST)
        if not form.is_valid():
            messages.error(request, "; ".join(f"{k}: {v[0]}" for k, v in form.errors.items()))
            return redirect(inv)
        d = form.cleaned_data
        try:
            services.pay_supplier_invoice(inv.pk, amount=d["amount"], method=d["method"], paid_on=d["paid_on"],
                                          reference=d["reference"], user=request.user,
                                          idempotency_key=d["token"] or None)
        except BusinessRuleError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Payment recorded.")
        return redirect(inv)
