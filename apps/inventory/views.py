from datetime import timedelta

import django_filters as df
from django import forms
from django.contrib import messages
from django.db.models import F, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from apps.core.csvutil import csv_response
from apps.core.exceptions import BusinessRuleError
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.products.models import Product

from . import services
from .forms import AdjustmentForm, StockTakeForm
from .models import StockMovement, StockTake, StockTakeLine


class StockFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search")
    category = df.ModelChoiceFilter(queryset=__import__("apps.products.models", fromlist=["Category"]).Category.objects.all())
    level = df.ChoiceFilter(method="by_level", empty_label="All", label="Level",
                            choices=[("low", "Low stock"), ("out", "Out of stock"), ("expiring", "Expiring in 30 days")])

    class Meta:
        model = Product
        fields = ["q", "category", "level"]

    def search(self, qs, name, value):
        from django.db.models import Q
        return qs.filter(Q(name__icontains=value) | Q(sku__icontains=value))

    def by_level(self, qs, name, value):
        if value == "low":
            return qs.filter(stock_on_hand__lte=F("reorder_level"))
        if value == "out":
            return qs.filter(stock_on_hand__lte=0)
        if value == "expiring":
            return qs.filter(expiry_date__lte=timezone.localdate() + timedelta(days=30),
                             stock_on_hand__gt=0)
        return qs


class StockLevelsView(FilteredListView):
    """Replacement for the legacy /inventory page (quantity, unit, category, availability)."""
    permission_required = "products.view_product"
    model = Product
    filterset_class = StockFilter
    template_name = "inventory/stock.html"
    title = "Inventory"

    def get_queryset(self):
        return super().get_queryset().filter(is_active=True).select_related("category").order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = Product.objects.filter(is_active=True)
        ctx["n_low"] = base.filter(stock_on_hand__lte=F("reorder_level"), stock_on_hand__gt=0).count()
        ctx["n_out"] = base.filter(stock_on_hand__lte=0).count()
        ctx["show_cost"] = self.request.user.has_perm("products.view_cost_price")
        if ctx["show_cost"]:
            ctx["valuation"] = sum((p.stock_value for p in base.only("cost_price", "stock_on_hand")), 0)
        return ctx


class MovementFilter(df.FilterSet):
    product = df.CharFilter(method="by_product", label="Product (name/SKU)")
    movement_type = df.ChoiceFilter(choices=StockMovement.Type.choices)
    date_from = df.DateFilter(field_name="created_at", lookup_expr="date__gte",
                              widget=forms.DateInput(attrs={"type": "date"}))
    date_to = df.DateFilter(field_name="created_at", lookup_expr="date__lte",
                            widget=forms.DateInput(attrs={"type": "date"}))

    class Meta:
        model = StockMovement
        fields = ["product", "movement_type", "date_from", "date_to"]

    def by_product(self, qs, name, value):
        from django.db.models import Q
        return qs.filter(Q(product__name__icontains=value) | Q(product__sku__icontains=value))


class MovementListView(FilteredListView):
    permission_required = "inventory.view_stockmovement"
    model = StockMovement
    filterset_class = MovementFilter
    title = "Stock movements"
    paginate_by = 50
    columns = (("When", "created_at", "datetime", "nolink"), ("Product", "product", "text", "nolink"),
               ("Type", "get_movement_type_display", "text"), ("Qty", "quantity", "num"),
               ("Balance after", "balance_after", "num"), ("By", "created_by", "text"),
               ("Note", "note", "text"))
    actions = (("Adjust stock", "inventory:adjust", "inventory.adjust_stock", "primary"),
               ("Stock takes", "inventory:stocktakes", "inventory.view_stocktake"),
               ("Reconciliation", "inventory:reconcile", "inventory.view_stockmovement"))

    def get_queryset(self):
        return super().get_queryset().select_related("product", "created_by")


class AdjustStockView(AppPermissionMixin, View):
    permission_required = "inventory.adjust_stock"

    def get(self, request):
        initial = {"product": request.GET.get("product")}
        return render(request, "generic/form.html", {
            "form": AdjustmentForm(initial=initial), "title": "Adjust stock",
            "cancel_url": reverse("inventory:stock")})

    def post(self, request):
        form = AdjustmentForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                m = services.adjust_stock(d["product"].pk, d["quantity"], kind=d["kind"],
                                          user=request.user, note=d["reason"])
            except BusinessRuleError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, f"Stock for {d['product'].name} is now {m.balance_after}.")
                return redirect("inventory:movements")
        return render(request, "generic/form.html", {"form": form, "title": "Adjust stock",
                                                     "cancel_url": reverse("inventory:stock")})


class StockTakeListView(AppPermissionMixin, ListView):
    permission_required = "inventory.view_stocktake"
    model = StockTake
    template_name = "inventory/stocktakes.html"

    def post(self, request):
        if not request.user.has_perm("inventory.add_stocktake"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        form = StockTakeForm(request.POST)
        if form.is_valid():
            st = services.start_stocktake(form.cleaned_data["name"], request.user)
            return redirect("inventory:stocktake", pk=st.pk)
        messages.error(request, "Enter a name for the stock take.")
        return redirect("inventory:stocktakes")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = StockTakeForm()
        return ctx


class StockTakeView(AppPermissionMixin, View):
    permission_required = "inventory.view_stocktake"

    def get(self, request, pk):
        st = get_object_or_404(StockTake, pk=pk)
        return render(request, "inventory/stocktake.html", {
            "st": st, "lines": st.lines.select_related("product").order_by("product__name")})

    def post(self, request, pk):
        if not request.user.has_perm("inventory.change_stocktake"):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        st = get_object_or_404(StockTake, pk=pk)
        if st.status != StockTake.Status.DRAFT:
            messages.error(request, "This stock take was already applied.")
            return redirect("inventory:stocktake", pk=pk)
        for line in st.lines.all():
            raw = request.POST.get(f"count_{line.pk}", "").strip()
            if raw == "":
                continue
            if not raw.isdigit():
                messages.error(request, f"Invalid count for line {line.pk}.")
                return redirect("inventory:stocktake", pk=pk)
            StockTakeLine.objects.filter(pk=line.pk).update(counted_quantity=int(raw))
        if "apply" in request.POST:
            try:
                n = services.apply_stocktake(pk, request.user)
            except BusinessRuleError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Stock take applied: {n} correction(s) posted.")
        else:
            messages.success(request, "Counts saved.")
        return redirect("inventory:stocktake", pk=pk)


class ReconcileView(AppPermissionMixin, View):
    permission_required = "inventory.view_stockmovement"

    def get(self, request):
        bad = services.reconcile()
        if request.GET.get("format") == "csv":
            return csv_response("stock_reconciliation.csv", ["sku", "name", "ledger_sum", "stock_on_hand"],
                                [(p.sku, p.name, a, b) for p, a, b in bad])
        return render(request, "inventory/reconcile.html", {
            "mismatches": bad, "checked": Product.objects.count(),
            "ledger_total": StockMovement.objects.aggregate(s=Sum("quantity"))["s"] or 0})
