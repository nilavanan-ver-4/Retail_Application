import io

from django import forms
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views import View

from apps.audit.services import log as audit_log
from apps.core.csvutil import csv_response, safe_cell
from apps.core.mixins import AppPermissionMixin

from . import queries as q

SALES, INVENTORY, FINANCIAL = (
    "reports.view_sales_reports",
    "reports.view_inventory_reports",
    "reports.view_financial_reports",
)

# key -> (title, group, permission, needs date range, description)
REPORTS = {
    "sales-summary": (
        "Sales summary",
        "Sales",
        SALES,
        True,
        "Daily, weekly, monthly, quarterly or yearly sales.",
    ),
    "sales-by-product": (
        "Sales by product",
        "Sales",
        SALES,
        True,
        "Units, revenue and estimated gross profit per product.",
    ),
    "top-products": ("Top-selling products", "Sales", SALES, True, "The 10 best sellers by units."),
    "sales-by-category": ("Sales by category", "Sales", SALES, True, "Revenue and profit per category."),
    "sales-by-customer": ("Sales by customer", "Sales", SALES, True, "What each customer bought."),
    "sales-by-supplier": (
        "Sales by supplier",
        "Sales",
        SALES,
        True,
        "Revenue grouped by the product's supplier.",
    ),
    "discounts": ("Discounts given", "Sales", SALES, True, "Line and invoice discounts."),
    "tax-summary": ("Tax summary", "Sales", FINANCIAL, True, "Taxable value and tax by rate."),
    "profit-estimate": (
        "Gross profit estimate",
        "Financial",
        FINANCIAL,
        True,
        "Revenue minus cost of goods sold (estimate).",
    ),
    "payment-methods": (
        "Payment-method breakdown",
        "Financial",
        FINANCIAL,
        True,
        "Cash collected by method.",
    ),
    "daily-collections": (
        "Daily collection summary",
        "Financial",
        FINANCIAL,
        True,
        "Net collections per day and method.",
    ),
    "receivables": (
        "Customer receivables",
        "Financial",
        FINANCIAL,
        False,
        "Outstanding balances with ageing.",
    ),
    "payables": ("Supplier payables", "Financial", FINANCIAL, False, "What is owed to suppliers."),
    "returns": ("Returned products", "Financial", FINANCIAL, True, "Approved returns and refunds value."),
    "reconciliation": (
        "Reconciliation checks",
        "Financial",
        FINANCIAL,
        False,
        "Invoices vs payments vs stock ledger.",
    ),
    "inventory-valuation": ("Inventory valuation", "Inventory", INVENTORY, False, "Stock value at cost."),
    "low-stock": (
        "Low stock & reorder",
        "Inventory",
        INVENTORY,
        False,
        "Products at or below reorder level.",
    ),
    "stock-ageing": (
        "Inventory ageing",
        "Inventory",
        INVENTORY,
        False,
        "How long current stock has been on hand (FIFO).",
    ),
    "purchases": ("Purchase history", "Inventory", INVENTORY, True, "Supplier invoices and receipts."),
}


class RangeForm(forms.Form):
    date_from = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), required=False)
    date_to = forms.DateField(widget=forms.DateInput(attrs={"type": "date"}), required=False)
    period = forms.ChoiceField(
        choices=[
            ("day", "Daily"),
            ("week", "Weekly"),
            ("month", "Monthly"),
            ("quarter", "Quarterly"),
            ("year", "Yearly"),
        ],
        required=False,
    )

    def clean(self):
        d = super().clean()
        today = timezone.localdate()
        d["date_from"] = d.get("date_from") or today.replace(day=1)
        d["date_to"] = d.get("date_to") or today
        if d["date_from"] > d["date_to"]:
            raise forms.ValidationError("The start date must not be after the end date.")
        if (d["date_to"] - d["date_from"]).days > 366 * 5:
            raise forms.ValidationError("Choose a range of five years or less.")
        d["period"] = d.get("period") or "day"
        return d


class ReportIndexView(AppPermissionMixin, View):
    permission_required = ()

    def has_permission(self):
        u = self.request.user
        return u.is_authenticated and any(u.has_perm(p) for p in (SALES, INVENTORY, FINANCIAL))

    def get(self, request):
        groups = {}
        for key, (title, group, perm, _, desc) in REPORTS.items():
            if request.user.has_perm(perm):
                groups.setdefault(group, []).append((key, title, desc))
        if request.user.has_perm("reports.view_audit_reports"):
            groups.setdefault("Audit", []).append(
                ("audit", "Transaction audit log", "Who changed what and when.")
            )
        return render(request, "reports/index.html", {"groups": groups})


def build(key, params):
    f, t, p = params["date_from"], params["date_to"], params["period"]
    return {
        "sales-summary": lambda: q.sales_summary(f, t, p),
        "sales-by-product": lambda: q.sales_by(f, t, "product"),
        "top-products": lambda: q.sales_by(f, t, "product", limit=10, order="qty"),
        "sales-by-category": lambda: q.sales_by(f, t, "category"),
        "sales-by-customer": lambda: q.sales_by(f, t, "customer"),
        "sales-by-supplier": lambda: q.sales_by(f, t, "supplier"),
        "discounts": lambda: q.discounts(f, t),
        "tax-summary": lambda: q.tax_summary(f, t),
        "profit-estimate": lambda: q.sales_by(f, t, "category"),
        "payment-methods": lambda: q.payment_methods(f, t),
        "daily-collections": lambda: q.daily_collections(f, t),
        "receivables": q.receivables,
        "payables": q.payables,
        "returns": lambda: q.returns_report(f, t),
        "reconciliation": q.reconciliation,
        "inventory-valuation": q.inventory_valuation,
        "low-stock": q.low_stock,
        "stock-ageing": q.stock_ageing,
        "purchases": lambda: q.purchases(f, t),
    }[key]()


def _cell(v):
    from datetime import date as _d

    if isinstance(v, _d):
        return v.isoformat()
    return v


class ReportView(AppPermissionMixin, View):
    permission_required = ()

    def dispatch(self, request, *args, **kwargs):
        key = kwargs.get("key")
        if key not in REPORTS:
            raise Http404
        self.permission_required = REPORTS[key][2]
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, key):
        title, group, perm, needs_range, desc = REPORTS[key]
        form = RangeForm(request.GET or None)
        if not request.GET:
            form = RangeForm(
                initial={"date_from": timezone.localdate().replace(day=1), "date_to": timezone.localdate()}
            )
            params = {
                "date_from": timezone.localdate().replace(day=1),
                "date_to": timezone.localdate(),
                "period": "day",
            }
        elif form.is_valid():
            params = form.cleaned_data
        else:
            return render(
                request,
                "reports/report.html",
                {"key": key, "title": title, "form": form, "needs_range": needs_range, "report": None},
            )
        report = build(key, params)
        fmt = request.GET.get("format")
        base = f"{key}_{params['date_from']}_{params['date_to']}"
        if fmt in ("csv", "xlsx", "pdf"):
            audit_log("report.exported", None, summary=f"{title} exported as {fmt}", report=key)
        if fmt == "csv":
            rows = [[_cell(c) for c in r] for r in report["rows"]]
            if report.get("totals"):
                rows.append(report["totals"])
            return csv_response(f"{base}.csv", [c[0] for c in report["columns"]], rows)
        if fmt == "xlsx":
            return self._xlsx(base, title, report)
        ctx = {
            "key": key,
            "title": title,
            "form": form,
            "needs_range": needs_range,
            "report": report,
            "params": params,
            "desc": desc,
            "show_period": key == "sales-summary",
        }
        if fmt == "pdf":
            from weasyprint import HTML

            from apps.sales.pdf import safe_fetcher

            html = render_to_string("reports/report_pdf.html", ctx, request=request)
            resp = HttpResponse(
                HTML(string=html, url_fetcher=safe_fetcher()).write_pdf(), content_type="application/pdf"
            )
            resp["Content-Disposition"] = f'inline; filename="{base}.pdf"'
            return resp
        return render(request, "reports/report.html", ctx)

    def _xlsx(self, base, title, report):
        from openpyxl import Workbook

        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title[:30])
        ws.append([c[0] for c in report["columns"]])
        for r in report["rows"]:
            ws.append([safe_cell(_cell(c)) if not hasattr(c, "as_tuple") else float(c) for c in r])
        if report.get("totals"):
            ws.append([float(c) if hasattr(c, "as_tuple") else c for c in report["totals"]])
        buf = io.BytesIO()
        wb.save(buf)
        resp = HttpResponse(
            buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp["Content-Disposition"] = f'attachment; filename="{base}.xlsx"'
        return resp
