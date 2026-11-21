from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import redirect, render
from django.views import View

from apps.audit.services import log as audit_log
from apps.core.csvutil import csv_response
from apps.core.mixins import AppPermissionMixin
from apps.sales.models import Invoice, InvoiceLine

from .models import Product, TaxConfiguration
from .tax_forms import ProductTaxForm, TaxConfigurationForm, TaxProductFilterForm, TaxSummaryFilterForm


class TaxView(AppPermissionMixin, View):
    permission_required = "products.view_taxconfiguration"

    def get(self, request):
        return self.render_page(request)

    def render_page(self, request, *, settings_form=None, product_form=None, status=200):
        configuration = TaxConfiguration.current()
        product_filter = TaxProductFilterForm(request.GET)
        products = Product.objects.select_related("category").order_by("name", "pk")
        if product_filter.is_valid():
            if q := product_filter.cleaned_data.get("q"):
                products = products.filter(Q(name__icontains=q) | Q(sku__icontains=q))
            if category := product_filter.cleaned_data.get("category"):
                products = products.filter(category=category)
        params = request.GET.copy()
        params.pop("page", None)
        return render(
            request,
            "taxes/index.html",
            {
                "configuration": configuration,
                "settings_form": settings_form
                if settings_form is not None
                else TaxConfigurationForm(instance=configuration),
                "product_form": product_form if product_form is not None else ProductTaxForm(),
                "product_filter": product_filter,
                "page_obj": Paginator(products, 25).get_page(request.GET.get("page")),
                "querystring": params.urlencode(),
                "rate_groups": Product.objects.values("tax_rate")
                .annotate(count=Count("pk"))
                .order_by("tax_rate"),
            },
            status=status,
        )

    def post(self, request):
        if not request.user.has_perm("products.change_taxconfiguration"):
            raise PermissionDenied
        action = request.POST.get("action")
        if action == "settings":
            current = TaxConfiguration.current()
            defaults = {
                field: getattr(current, field) for field in ("label", "registration_number", "default_rate")
            }
            form = TaxConfigurationForm(request.POST, instance=current)
            if not form.is_valid():
                return self.render_page(request, settings_form=form, status=400)
            with transaction.atomic():
                TaxConfiguration.objects.get_or_create(pk=1, defaults=defaults)
                configuration = TaxConfiguration.objects.select_for_update().get(pk=1)
                before = {
                    field: str(getattr(configuration, field))
                    for field in ("label", "registration_number", "default_rate")
                }
                for field, value in form.cleaned_data.items():
                    setattr(configuration, field, value)
                configuration.save()
                audit_log("tax.settings_updated", configuration, before=before, after=form.cleaned_data)
            messages.success(
                request,
                "Tax settings saved. The default applies to new products; existing product rates and invoices are unchanged.",
            )
        elif action == "products":
            if not request.user.has_perm("products.change_product"):
                raise PermissionDenied
            form = ProductTaxForm(request.POST)
            if not form.is_valid():
                return self.render_page(request, product_form=form, status=400)
            with transaction.atomic():
                products = list(
                    Product.objects.select_for_update()
                    .filter(pk__in=form.cleaned_data["products"].values("pk"))
                    .order_by("pk")
                )
                for product in products:
                    old = product.tax_rate
                    product.tax_rate = form.cleaned_data["rate"]
                    product.save(update_fields=["tax_rate", "updated_at"])
                    audit_log(
                        "product.tax_updated",
                        product,
                        old_rate=str(old),
                        new_rate=str(product.tax_rate),
                        reason=form.cleaned_data["reason"],
                    )
            messages.success(
                request,
                f"Tax updated for {len(products)} products. New sales use these rates; issued invoices keep their original amounts.",
            )
        else:
            messages.error(request, "Choose a valid tax action.")
        return redirect("taxes:index")


class TaxSummaryView(AppPermissionMixin, View):
    permission_required = ("products.view_taxconfiguration", "reports.view_financial_reports")

    def get(self, request):
        form = TaxSummaryFilterForm(request.GET)
        lines = InvoiceLine.objects.exclude(invoice__status=Invoice.Status.CANCELLED)
        if form.is_valid():
            if start := form.cleaned_data.get("start"):
                lines = lines.filter(invoice__date__gte=start)
            if end := form.cleaned_data.get("end"):
                lines = lines.filter(invoice__date__lte=end)
        else:
            lines = lines.none()
        groups = list(
            lines.values("invoice__tax_label", "tax_rate")
            .annotate(
                taxable=Sum("taxable_amount"),
                tax=Sum("tax_amount"),
                invoices=Count("invoice_id", distinct=True),
            )
            .order_by("invoice__tax_label", "tax_rate")
        )
        if request.GET.get("format") == "csv" and form.is_valid():
            return csv_response(
                "invoice-tax-summary.csv",
                ["Tax label", "Rate (%)", "Invoices", "Base after discounts", "Tax"],
                [
                    [
                        group["invoice__tax_label"],
                        group["tax_rate"],
                        group["invoices"],
                        group["taxable"],
                        group["tax"],
                    ]
                    for group in groups
                ],
            )
        return render(
            request,
            "taxes/summary.html",
            {
                "form": form,
                "groups": groups,
                "total_tax": sum((group["tax"] for group in groups), Decimal("0.00")),
                "querystring": request.GET.urlencode(),
            },
            status=200 if form.is_valid() else 400,
        )
