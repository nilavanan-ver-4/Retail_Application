import django_filters as df
from django import forms
from django.contrib import messages
from django.db.models import Case, DecimalField, F, Q, Sum, When
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, UpdateView

from apps.audit.services import log as audit_log
from apps.core.csvutil import csv_response
from apps.core.import_views import ImportPreviewView, ImportUploadView
from apps.core.mixins import AppPermissionMixin, FilteredListView

from .forms import CustomerForm, ManualLedgerForm
from .importers import CustomerImporter
from .models import Customer, LedgerEntry
from .services import outstanding_for, post_ledger_entry

_OWED = Case(
    When(
        ~Q(invoices__status="Cancelled"),
        then=F("invoices__total") - F("invoices__returned_total") - F("invoices__total_paid"),
    ),
    default=0,
    output_field=DecimalField(max_digits=14, decimal_places=2),
)


class CustomerFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search (name, mobile, email)")
    owing = df.BooleanFilter(method="by_owing", label="Has outstanding balance", widget=forms.CheckboxInput)
    status = df.ChoiceFilter(
        method="by_status", empty_label="Active only", choices=[("inactive", "Inactive"), ("all", "All")]
    )

    class Meta:
        model = Customer
        fields = ["q", "owing", "status"]

    def search(self, qs, name, value):
        return qs.filter(Q(name__icontains=value) | Q(mobile__icontains=value) | Q(email__icontains=value))

    def by_owing(self, qs, name, value):
        return qs.filter(outstanding__gt=0) if value else qs

    def by_status(self, qs, name, value):
        return qs.filter(is_active=False) if value == "inactive" else qs

    @property
    def qs(self):
        qs = super().qs
        if self.data.get("status") not in ("inactive", "all"):
            qs = qs.filter(is_active=True)
        return qs


class CustomerListView(FilteredListView):
    permission_required = "customers.view_customer"
    model = Customer
    filterset_class = CustomerFilter
    title = "Customers"
    columns = (
        ("Name", "name"),
        ("Mobile", "mobile", "text", "nolink"),
        ("Email", "email"),
        ("Outstanding", "outstanding", "money"),
        ("Credit limit", "credit_limit", "money"),
        ("Since", "created_at", "date"),
    )
    actions = (
        ("Add customer", "customers:create", "customers.add_customer", "primary"),
        ("Import", "customers:import", "customers.add_customer"),
        ("Export CSV", "customers:export", "customers.view_customer"),
        ("Ledger", "customers:ledger", "customers.view_ledgerentry"),
    )

    def get_queryset(self):
        self.queryset = Customer.objects.annotate(outstanding=Sum(_OWED)).order_by("name")
        return super().get_queryset()


class CustomerDetailView(AppPermissionMixin, DetailView):
    permission_required = "customers.view_customer"
    model = Customer
    template_name = "customers/detail.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        c = self.object
        ctx["outstanding"] = outstanding_for(c.pk)
        ctx["invoices"] = c.invoices.order_by("-date", "-id")[:20]
        ctx["ledger"] = c.ledger_entries.all()[:20]
        return ctx


class CustomerCreateView(AppPermissionMixin, CreateView):
    permission_required = "customers.add_customer"
    model = Customer
    form_class = CustomerForm
    template_name = "generic/form.html"
    extra_context = {"title": "Add customer", "cancel_url": reverse_lazy("customers:list")}

    def form_valid(self, form):
        resp = super().form_valid(form)
        audit_log("customer.created", self.object)
        messages.success(self.request, "Customer added.")
        return resp


class CustomerUpdateView(AppPermissionMixin, UpdateView):
    permission_required = "customers.change_customer"
    model = Customer
    form_class = CustomerForm
    template_name = "generic/form.html"
    extra_context = {"title": "Edit customer", "cancel_url": reverse_lazy("customers:list")}

    def form_valid(self, form):
        resp = super().form_valid(form)
        if "credit_limit" in form.changed_data:
            audit_log("customer.credit_limit_changed", self.object, new=str(self.object.credit_limit))
        messages.success(self.request, "Customer updated.")
        return resp


class CustomerExportView(AppPermissionMixin, View):
    permission_required = "customers.view_customer"

    def get(self, request):
        qs = CustomerFilter(request.GET or None, queryset=Customer.objects.all()).qs
        rows = [
            (
                c.name,
                c.mobile,
                c.email,
                c.billing_address,
                c.shipping_address,
                c.credit_limit or "",
                "yes" if c.is_active else "no",
            )
            for c in qs.iterator()
        ]
        audit_log("customer.exported", None, summary=f"{len(rows)} customers exported")
        return csv_response(
            "customers.csv",
            ["name", "mobile", "email", "billing_address", "shipping_address", "credit_limit", "active"],
            rows,
        )


class CustomerImportUploadView(ImportUploadView):
    permission_required = "customers.add_customer"
    importer_class = CustomerImporter
    title = "Import customers"
    back_url = reverse_lazy("customers:list")
    preview_url_name = "customers:import_preview"


class CustomerImportPreviewView(ImportPreviewView):
    permission_required = "customers.add_customer"
    importer_class = CustomerImporter
    title = "Customer import preview"
    done_url_name = "customers:list"


class LedgerFilter(df.FilterSet):
    customer = df.CharFilter(method="by_customer", label="Customer (name/mobile)")
    entry_type = df.ChoiceFilter(choices=LedgerEntry.Type.choices)

    class Meta:
        model = LedgerEntry
        fields = ["customer", "entry_type"]

    def by_customer(self, qs, name, value):
        return qs.filter(Q(customer__name__icontains=value) | Q(customer__mobile__icontains=value))


class LedgerListView(FilteredListView):
    """Legacy /ledger page."""

    permission_required = "customers.view_ledgerentry"
    model = LedgerEntry
    filterset_class = LedgerFilter
    title = "Customer ledger"
    paginate_by = 50
    columns = (
        ("Date", "entry_date", "datetime", "nolink"),
        ("Customer", "customer", "text", "nolink"),
        ("Type", "entry_type", "text"),
        ("Amount", "amount", "money"),
        ("Balance", "balance", "money"),
        ("Description", "description", "text"),
    )
    actions = (("Manual entry", "customers:ledger_add", "customers.add_ledgerentry", "primary"),)

    def get_queryset(self):
        return super().get_queryset().select_related("customer")


class LedgerAddView(AppPermissionMixin, View):
    permission_required = "customers.add_ledgerentry"

    def get(self, request):
        return render(
            request,
            "generic/form.html",
            {
                "form": ManualLedgerForm(),
                "title": "Manual ledger entry",
                "cancel_url": reverse("customers:ledger"),
            },
        )

    def post(self, request):
        form = ManualLedgerForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            e = post_ledger_entry(
                d["customer"].pk,
                d["entry_type"],
                d["amount"],
                user=request.user,
                description=d["description"],
                source=LedgerEntry.Source.MANUAL,
            )
            audit_log(
                "ledger.manual_entry",
                e,
                customer=d["customer"].pk,
                type=d["entry_type"],
                amount=str(d["amount"]),
                reason=d["description"],
            )
            messages.success(request, "Ledger entry recorded.")
            return redirect("customers:ledger")
        return render(
            request,
            "generic/form.html",
            {"form": form, "title": "Manual ledger entry", "cancel_url": reverse("customers:ledger")},
        )


class CustomerLookupView(AppPermissionMixin, View):
    """JSON lookup by mobile for the billing screen (legacy /get_customer_details)."""

    permission_required = "customers.view_customer"

    def get(self, request):
        from django.http import JsonResponse

        c = Customer.objects.filter(mobile=request.GET.get("mobile", "").strip(), is_active=True).first()
        if not c:
            return JsonResponse({"exists": False})
        return JsonResponse(
            {
                "exists": True,
                "id": c.pk,
                "name": c.name,
                "address": c.billing_address,
                "mail": c.email,
                "credit_limit": str(c.credit_limit) if c.credit_limit else None,
                "outstanding": str(outstanding_for(c.pk)),
            }
        )
