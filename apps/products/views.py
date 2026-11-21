from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from apps.audit.services import log as audit_log
from apps.core.csvutil import csv_response
from apps.core.exceptions import BusinessRuleError
from apps.core.import_views import ImportPreviewView, ImportUploadView
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.core.money import q2
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement

from .filters import ProductFilter
from .forms import BrandForm, BulkActionForm, CategoryForm, ProductForm
from .importers import ProductImporter
from .models import Brand, Category, Product

PRICE_FIELDS = ("selling_price", "cost_price", "tax_rate")


class ProductListView(FilteredListView):
    permission_required = "products.view_product"
    model = Product
    filterset_class = ProductFilter
    template_name = "products/list.html"
    title = "Products"
    paginate_by = 25

    def get_queryset(self):
        return super().get_queryset().select_related("category", "brand")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["show_cost"] = self.request.user.has_perm("products.view_cost_price")
        ctx["bulk_form"] = BulkActionForm()
        ctx["categories"] = Category.objects.all()
        return ctx

    def get_actions(self):
        u = self.request.user
        acts = []
        if u.has_perm("products.add_product"):
            acts.append({"label": "Add product", "url": reverse("products:create"), "kind": "primary"})
        if u.has_perm("products.import_products"):
            acts.append({"label": "Import", "url": reverse("products:import"), "kind": ""})
        if u.has_perm("products.export_products"):
            acts.append(
                {
                    "label": "Export CSV",
                    "url": reverse("products:export") + "?" + self.request.GET.urlencode(),
                    "kind": "",
                }
            )
        return acts


class ProductDetailView(AppPermissionMixin, DetailView):
    permission_required = "products.view_product"
    model = Product
    template_name = "products/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["show_cost"] = self.request.user.has_perm("products.view_cost_price")
        ctx["movements"] = self.object.movements.select_related("created_by")[:15]
        return ctx


class ProductFormMixin:
    model = Product
    form_class = ProductForm
    template_name = "products/form.html"

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["user"] = self.request.user
        return kw


class ProductCreateView(ProductFormMixin, AppPermissionMixin, CreateView):
    permission_required = "products.add_product"

    @transaction.atomic
    def form_valid(self, form):
        product = form.save(commit=False)
        product.save()
        opening = form.cleaned_data.get("opening_stock") or 0
        if opening:
            apply_movement(
                product.pk,
                StockMovement.Type.OPENING,
                opening,
                user=self.request.user,
                note="Opening stock at product creation",
                unit_cost=product.cost_price,
            )
        audit_log("product.created", product, price=str(product.selling_price))
        messages.success(self.request, f"Product {product.name} created.")
        self.object = product
        return redirect(product.get_absolute_url())


class ProductUpdateView(ProductFormMixin, AppPermissionMixin, UpdateView):
    permission_required = "products.change_product"

    def form_valid(self, form):
        before = {f: getattr(Product.objects.get(pk=self.object.pk), f) for f in PRICE_FIELDS}
        with transaction.atomic():
            product = form.save()
            product.refresh_from_db()  # normalised Decimal values, as stored
            after = {f: getattr(product, f) for f in PRICE_FIELDS}
            changed = {f: [str(before[f]), str(after[f])] for f in PRICE_FIELDS if before[f] != after[f]}
            if changed:
                audit_log("product.price_changed", product, **changed)
        messages.success(self.request, "Product updated.")
        return redirect(product.get_absolute_url())


class ProductArchiveView(AppPermissionMixin, View):
    permission_required = "products.change_product"
    restore = False

    def post(self, request, pk):
        p = get_object_or_404(Product, pk=pk)
        p.restore() if self.restore else p.archive()
        audit_log("product.restored" if self.restore else "product.archived", p)
        messages.success(
            request, f"{p.name} {'restored' if self.restore else 'archived'}. Sales history is kept."
        )
        return redirect(p.get_absolute_url())


class ProductBulkView(AppPermissionMixin, View):
    permission_required = "products.change_product"

    def post(self, request):
        form = BulkActionForm(request.POST)
        ids = [int(i) for i in request.POST.getlist("selected") if i.isdigit()]
        if not form.is_valid() or not ids:
            messages.error(request, "Choose an action and at least one product.")
            return redirect("products:list")
        action, value = form.cleaned_data["action"], form.cleaned_data["value"].strip()
        qs = Product.objects.filter(pk__in=ids)
        with transaction.atomic():
            products = list(qs.select_for_update())
            try:
                for p in products:
                    self._apply(p, action, value)
            except (BusinessRuleError, ValueError, InvalidOperation, Category.DoesNotExist) as exc:
                transaction.set_rollback(True)
                messages.error(request, f"Bulk update failed: {exc or 'invalid value'}. Nothing was changed.")
                return redirect("products:list")
            audit_log(
                "product.bulk_update",
                None,
                summary=f"{action} on {len(products)} products",
                bulk_action=action,
                value=value,
                ids=[p.pk for p in products],
            )
        messages.success(request, f"Updated {len(products)} product(s).")
        return redirect("products:list")

    def _apply(self, p, action, value):
        if action == "archive":
            p.archive()
        elif action == "restore":
            p.restore()
        elif action == "category":
            p.category = Category.objects.get(pk=int(value))
            p.save(update_fields=["category", "updated_at"])
        elif action == "tax_rate":
            rate = Decimal(value)
            if not Decimal(0) <= rate <= Decimal(100):
                raise ValueError("tax rate must be 0-100")
            old, p.tax_rate = p.tax_rate, q2(rate)
            p.save(update_fields=["tax_rate", "updated_at"])
            audit_log("product.price_changed", p, tax_rate=[str(old), str(p.tax_rate)], via="bulk")
        elif action == "reorder_level":
            level = int(value)
            if level < 0:
                raise ValueError("reorder level cannot be negative")
            p.reorder_level = level
            p.save(update_fields=["reorder_level", "updated_at"])
        elif action == "price_percent":
            pct = Decimal(value)
            if pct <= Decimal(-100):
                raise ValueError("percentage must be above -100")
            old, p.selling_price = p.selling_price, q2(p.selling_price * (1 + pct / 100))
            p.save(update_fields=["selling_price", "updated_at"])
            audit_log("product.price_changed", p, selling_price=[str(old), str(p.selling_price)], via="bulk")


class ProductExportView(AppPermissionMixin, View):
    permission_required = "products.export_products"

    def get(self, request):
        qs = ProductFilter(
            request.GET or None, queryset=Product.objects.select_related("category", "brand", "supplier")
        ).qs
        show_cost = request.user.has_perm("products.view_cost_price")
        header = [
            "sku",
            "name",
            "category",
            "brand",
            "barcode",
            "unit",
            "selling_price",
            "tax_rate",
            "stock_on_hand",
            "reorder_level",
            "expiry_date",
            "active",
        ]
        if show_cost:
            header.insert(6, "cost_price")
        rows = []
        for p in qs.iterator():
            row = [
                p.sku,
                p.name,
                p.category.name,
                p.brand.name if p.brand else "",
                p.barcode or "",
                p.unit,
                p.selling_price,
                p.tax_rate,
                p.stock_on_hand,
                p.reorder_level,
                p.expiry_date or "",
                "yes" if p.is_active else "no",
            ]
            if show_cost:
                row.insert(6, p.cost_price)
            rows.append(row)
        audit_log("product.exported", None, summary=f"{len(rows)} products exported")
        return csv_response("products.csv", header, rows)


class ProductImportUploadView(ImportUploadView):
    permission_required = "products.import_products"
    importer_class = ProductImporter
    title = "Import products"
    back_url = reverse_lazy("products:list")
    preview_url_name = "products:import_preview"


class ProductImportPreviewView(ImportPreviewView):
    permission_required = "products.import_products"
    importer_class = ProductImporter
    title = "Product import preview"
    done_url_name = "products:list"


class ProductSearchView(AppPermissionMixin, View):
    """JSON lookup used by the billing screen. Never exposes cost prices."""

    permission_required = "products.view_product"

    def get(self, request):
        q = request.GET.get("q", "").strip()
        from django.db.models import Case, IntegerField, Q, Value, When

        qs = Product.objects.filter(is_active=True)
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(barcode=q))
        else:
            qs = qs.none()
        return JsonResponse(
            {
                "products": [
                    {
                        "id": p.pk,
                        "sku": p.sku,
                        "name": p.name,
                        "price": str(p.selling_price),
                        "tax_rate": str(p.tax_rate),
                        "stock": p.stock_on_hand,
                        "unit": p.unit,
                    }
                    for p in qs.annotate(
                        exact_match=Case(
                            When(Q(barcode=q) | Q(sku__iexact=q), then=Value(0)),
                            default=Value(1),
                            output_field=IntegerField(),
                        )
                    ).order_by("exact_match", "name", "pk")[:12]
                ]
            }
        )


class CategoryListView(AppPermissionMixin, ListView):
    permission_required = "products.view_category"
    model = Category
    template_name = "products/categories.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["brands"] = Brand.objects.all()
        ctx["category_form"] = CategoryForm()
        ctx["brand_form"] = BrandForm(prefix="brand")
        return ctx

    def post(self, request):
        if not request.user.has_perm("products.add_category"):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        which = request.POST.get("which")
        form = BrandForm(request.POST, prefix="brand") if which == "brand" else CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"{which.title() if which else 'Category'} added.")
        else:
            messages.error(request, "; ".join(sum(form.errors.values(), [])))
        return redirect("products:categories")
