import django_filters as df
from django.contrib import messages
from django.db.models import Q, Sum
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, UpdateView

from apps.audit.services import log as audit_log
from apps.core.import_views import ImportPreviewView, ImportUploadView
from apps.core.mixins import AppPermissionMixin, FilteredListView
from apps.core.money import ZERO

from .forms import SupplierForm
from .importers import SupplierImporter
from .models import Supplier


class SupplierFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search")

    class Meta:
        model = Supplier
        fields = ["q"]

    def search(self, qs, name, value):
        return qs.filter(Q(name__icontains=value) | Q(company__icontains=value) | Q(mobile__icontains=value))


class SupplierListView(FilteredListView):
    permission_required = "suppliers.view_supplier"
    model = Supplier
    filterset_class = SupplierFilter
    title = "Suppliers"
    columns = (("Name", "name"), ("Company", "company", "text", "nolink"), ("Mobile", "mobile"),
               ("Email", "email"), ("Terms", "terms"), ("Active", "is_active", "bool"))
    actions = (("Add supplier", "suppliers:create", "suppliers.add_supplier", "primary"),
               ("Import", "suppliers:import", "suppliers.add_supplier"))


class SupplierDetailView(AppPermissionMixin, DetailView):
    permission_required = "suppliers.view_supplier"
    model = Supplier
    template_name = "suppliers/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        s = self.object
        ctx["payable"] = (s.invoices.exclude(status="Cancelled")
                          .aggregate(t=Sum("total_amount"), p=Sum("paid_amount")))
        ctx["payable"] = (ctx["payable"]["t"] or ZERO) - (ctx["payable"]["p"] or ZERO)
        ctx["invoices"] = s.invoices.all()[:15]
        ctx["orders"] = s.purchase_orders.all()[:15]
        return ctx


class SupplierCreateView(AppPermissionMixin, CreateView):
    permission_required = "suppliers.add_supplier"
    model = Supplier
    form_class = SupplierForm
    template_name = "generic/form.html"
    extra_context = {"title": "Add supplier", "cancel_url": reverse_lazy("suppliers:list")}

    def form_valid(self, form):
        resp = super().form_valid(form)
        audit_log("supplier.created", self.object)
        messages.success(self.request, "Supplier added.")
        return resp


class SupplierUpdateView(AppPermissionMixin, UpdateView):
    permission_required = "suppliers.change_supplier"
    model = Supplier
    form_class = SupplierForm
    template_name = "generic/form.html"
    extra_context = {"title": "Edit supplier", "cancel_url": reverse_lazy("suppliers:list")}

    def form_valid(self, form):
        messages.success(self.request, "Supplier updated.")
        return super().form_valid(form)


class SupplierImportUploadView(ImportUploadView):
    permission_required = "suppliers.add_supplier"
    importer_class = SupplierImporter
    title = "Import suppliers"
    back_url = reverse_lazy("suppliers:list")
    preview_url_name = "suppliers:import_preview"


class SupplierImportPreviewView(ImportPreviewView):
    permission_required = "suppliers.add_supplier"
    importer_class = SupplierImporter
    title = "Supplier import preview"
    done_url_name = "suppliers:list"
