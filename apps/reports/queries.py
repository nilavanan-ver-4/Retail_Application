"""Report queries - one function per report, all using database aggregation.

Definitions (also in docs/BUSINESS_RULES.md):
  * Invoiced sales   = sum(invoice.total) for non-cancelled invoices (includes tax and shipping).
  * Returns credited = sum(approved SalesReturn.credit_amount) by return date.
  * Cash collected   = completed PAYMENT rows minus completed REFUND rows, by payment date.
    Revenue (invoiced) and cash collected are different numbers and are never mixed.
  * Line revenue     = sum(taxable_amount): after all discounts, before tax.
  * Gross profit (ESTIMATE) = line revenue - quantity x unit_cost snapshot. Net profit is NOT
    calculated: the system holds no operating-expense data.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.db.models.functions import Coalesce, TruncDay, TruncMonth, TruncQuarter, TruncWeek, TruncYear
from django.utils import timezone

from apps.core.money import ZERO
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.inventory.services import fifo_ageing
from apps.payments.models import Payment
from apps.products.models import Product
from apps.purchasing.models import GoodsReceipt, PurchaseOrder, SupplierInvoice
from apps.returns.models import SalesReturn, SalesReturnLine
from apps.sales.models import Invoice, InvoiceLine

MONEY = DecimalField(max_digits=16, decimal_places=2)
TRUNC = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth, "quarter": TruncQuarter, "year": TruncYear}


def live_invoices(d_from, d_to):
    return Invoice.objects.filter(date__gte=d_from, date__lte=d_to).exclude(status=Invoice.Status.CANCELLED)


def _fmt(period, bucket):
    d = bucket
    if period == "day":
        return d.strftime("%Y-%m-%d")
    if period == "week":
        return "Wk " + d.strftime("%Y-%m-%d")
    if period == "month":
        return d.strftime("%Y-%m")
    if period == "quarter":
        return f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    return str(d.year)


def sales_summary(d_from, d_to, period="day"):
    trunc = TRUNC[period]
    inv = (
        live_invoices(d_from, d_to)
        .annotate(b=trunc("date"))
        .values("b")
        .annotate(
            n=Count("id"),
            sub=Sum("subtotal"),
            disc=Sum("discount_amount"),
            tax=Sum("tax_total"),
            ship=Sum("shipping_charge"),
            total=Sum("total"),
        )
        .order_by("b")
    )
    rets = defaultdict(lambda: ZERO)
    for r in (
        SalesReturn.objects.filter(status="approved", return_date__gte=d_from, return_date__lte=d_to)
        .annotate(b=trunc("return_date"))
        .values("b")
        .annotate(s=Sum("credit_amount"))
    ):
        rets[r["b"]] = r["s"]
    rows, totals = [], defaultdict(lambda: ZERO)
    for r in inv:
        credited = rets.pop(r["b"], ZERO)
        net = r["total"] - credited
        rows.append(
            [
                _fmt(period, r["b"]),
                r["n"],
                r["sub"],
                r["disc"],
                r["tax"],
                r["ship"],
                r["total"],
                credited,
                net,
            ]
        )
        for k, v in zip(
            ("n", "sub", "disc", "tax", "ship", "total", "credited", "net"),
            (r["n"], r["sub"], r["disc"], r["tax"], r["ship"], r["total"], credited, net),
            strict=True,
        ):
            totals[k] += v
    for b, credited in sorted(rets.items()):  # returns in periods with no invoices
        rows.append([_fmt(period, b), 0, ZERO, ZERO, ZERO, ZERO, ZERO, credited, -credited])
        totals["credited"] += credited
        totals["net"] -= credited
    rows.sort(key=lambda r: r[0])
    return {
        "columns": [
            ("Period", "text"),
            ("Invoices", "num"),
            ("Subtotal", "money"),
            ("Discounts", "money"),
            ("Tax", "money"),
            ("Shipping", "money"),
            ("Invoiced total", "money"),
            ("Returns credited", "money"),
            ("Net invoiced", "money"),
        ],
        "rows": rows,
        "totals": [
            "Total",
            totals["n"],
            totals["sub"],
            totals["disc"],
            totals["tax"],
            totals["ship"],
            totals["total"],
            totals["credited"],
            totals["net"],
        ],
        "chart": ([r[0] for r in rows], [r[8] for r in rows]),
        "notes": [
            "Invoiced total includes tax and shipping and excludes cancelled invoices.",
            "This is revenue billed, not cash collected - see the payments reports for collections.",
        ],
    }


DIMENSIONS = {
    "product": ("product__name", "product__sku"),
    "category": ("product__category__name", None),
    "customer": ("invoice__customer_name", "invoice__customer_mobile"),
    "supplier": ("product__supplier__name", None),
}


def sales_by(d_from, d_to, dimension="product", limit=None, order="revenue"):
    label, extra = DIMENSIONS[dimension]
    qs = InvoiceLine.objects.filter(invoice__date__gte=d_from, invoice__date__lte=d_to).exclude(
        invoice__status=Invoice.Status.CANCELLED
    )
    cost = Sum(
        Case(
            When(unit_cost__isnull=False, then=F("unit_cost") * F("quantity")),
            default=Value(0),
            output_field=MONEY,
        )
    )
    costed = Sum(
        Case(When(unit_cost__isnull=False, then=F("taxable_amount")), default=Value(0), output_field=MONEY)
    )
    fields = [label] + ([extra] if extra else [])
    agg = (
        qs.values(*fields)
        .annotate(
            qty=Sum("quantity"),
            revenue=Sum("taxable_amount"),
            tax=Sum("tax_amount"),
            cost=cost,
            costed_rev=costed,
        )
        .order_by("-qty" if order == "qty" else "-revenue")
    )
    if limit:
        agg = agg[:limit]
    rows = []
    for r in agg:
        gp = r["costed_rev"] - r["cost"]
        rows.append(
            [
                r[label] or "(none)",
                *([r[extra]] if extra else []),
                r["qty"],
                r["revenue"],
                r["tax"],
                r["cost"],
                gp,
            ]
        )
    cols = (
        [(dimension.title(), "text")]
        + ([("SKU" if dimension == "product" else "Mobile", "text")] if extra else [])
        + [
            ("Units", "num"),
            ("Revenue (ex-tax)", "money"),
            ("Tax", "money"),
            ("Cost (est.)", "money"),
            ("Gross profit (est.)", "money"),
        ]
    )
    off = 1 + (1 if extra else 0)
    totals = (
        ["Total"]
        + [""] * (off - 1)
        + [sum(r[off] for r in rows)]
        + [sum((r[i] for r in rows), ZERO) for i in range(off + 1, off + 5)]
    )
    return {
        "columns": cols,
        "rows": rows,
        "totals": totals,
        "chart": ([str(r[0])[:12] for r in rows[:12]], [r[off + 1] for r in rows[:12]]),
        "notes": [
            "Revenue is after line and invoice discounts, before tax.",
            "Cost uses the cost price captured when each item was sold. Legacy (migrated) invoices use "
            "the product's current cost, so their profit is an estimate. Net profit is not calculated "
            "because no operating expenses are recorded.",
        ],
    }


def tax_summary(d_from, d_to):
    qs = (
        InvoiceLine.objects.filter(invoice__date__gte=d_from, invoice__date__lte=d_to)
        .exclude(invoice__status=Invoice.Status.CANCELLED)
        .values("tax_rate")
        .annotate(taxable=Sum("taxable_amount"), tax=Sum("tax_amount"), n=Count("id"))
        .order_by("tax_rate")
    )
    rows = [[f"{r['tax_rate']}%", r["n"], r["taxable"], r["tax"]] for r in qs]
    return {
        "columns": [("Tax rate", "text"), ("Lines", "num"), ("Taxable value", "money"), ("Tax", "money")],
        "rows": rows,
        "totals": [
            "Total",
            sum(r[1] for r in rows),
            sum((r[2] for r in rows), ZERO),
            sum((r[3] for r in rows), ZERO),
        ],
        "notes": [
            "Tax is computed per line on the discounted value using each product's configured rate. "
            "Credit notes (returns) are not netted here."
        ],
    }


def discounts(d_from, d_to):
    inv = live_invoices(d_from, d_to).aggregate(inv=Sum("discount_amount"))["inv"] or ZERO
    lines = (
        InvoiceLine.objects.filter(invoice__date__gte=d_from, invoice__date__lte=d_to)
        .exclude(invoice__status=Invoice.Status.CANCELLED)
        .aggregate(s=Sum("discount"))["s"]
        or ZERO
    )
    top = live_invoices(d_from, d_to).filter(discount_amount__gt=0).order_by("-discount_amount")[:25]
    rows = [
        [i.number, i.date, i.customer_name, i.sold_by, i.discount_percent, i.discount_amount] for i in top
    ]
    return {
        "columns": [
            ("Invoice", "text"),
            ("Date", "date"),
            ("Customer", "text"),
            ("Sold by", "text"),
            ("Discount %", "num"),
            ("Discount", "money"),
        ],
        "rows": rows,
        "totals": ["Line discounts", "", "", "", "", lines],
        "summary": [
            ("Invoice-level discounts", inv),
            ("Line-level discounts", lines),
            ("Total given", inv + lines),
        ],
        "notes": ["Largest invoice-level discounts shown (top 25)."],
    }


def payment_methods(d_from, d_to):
    base = Payment.objects.filter(status="completed", paid_on__gte=d_from, paid_on__lte=d_to)
    pay = {
        r["method"]: r["s"] for r in base.filter(kind="payment").values("method").annotate(s=Sum("amount"))
    }
    ref = {r["method"]: r["s"] for r in base.filter(kind="refund").values("method").annotate(s=Sum("amount"))}
    labels = dict(Payment.Method.choices)
    rows = []
    for m in sorted(set(pay) | set(ref)):
        rows.append(
            [labels.get(m, m), pay.get(m, ZERO), ref.get(m, ZERO), pay.get(m, ZERO) - ref.get(m, ZERO)]
        )
    return {
        "columns": [
            ("Method", "text"),
            ("Received", "money"),
            ("Refunded", "money"),
            ("Net collected", "money"),
        ],
        "rows": rows,
        "totals": ["Total"] + [sum((r[i] for r in rows), ZERO) for i in (1, 2, 3)],
        "chart": ([r[0] for r in rows], [r[3] for r in rows]),
        "notes": [
            "Cash collected by payment date. Reversed payments and legacy placeholder rows are excluded."
        ],
    }


def daily_collections(d_from, d_to):
    base = Payment.objects.filter(status="completed", paid_on__gte=d_from, paid_on__lte=d_to)
    data = defaultdict(lambda: defaultdict(lambda: ZERO))
    methods = set()
    for r in base.values("paid_on", "method", "kind").annotate(s=Sum("amount")):
        sign = 1 if r["kind"] == "payment" else -1
        data[r["paid_on"]][r["method"]] += sign * r["s"]
        methods.add(r["method"])
    methods = sorted(methods)
    rows = [
        [d] + [data[d].get(m, ZERO) for m in methods] + [sum(data[d].values(), ZERO)] for d in sorted(data)
    ]
    labels = dict(Payment.Method.choices)
    cols = [("Date", "date")] + [(labels.get(m, m), "money") for m in methods] + [("Net collected", "money")]
    totals = ["Total"] + [sum((r[i] for r in rows), ZERO) for i in range(1, len(methods) + 2)]
    return {
        "columns": cols,
        "rows": rows,
        "totals": totals,
        "chart": ([r[0].strftime("%m-%d") for r in rows], [r[-1] for r in rows]),
        "notes": ["Net of refunds. One row per day."],
    }


def receivables(as_of=None):
    today = as_of or timezone.localdate()
    rows = []
    buckets = defaultdict(lambda: [ZERO] * 4)
    names = {}
    qs = (
        Invoice.objects.exclude(status__in=[Invoice.Status.CANCELLED])
        .annotate(due=F("total") - F("returned_total") - F("total_paid"))
        .filter(due__gt=0)
        .select_related("customer")
    )
    for inv in qs:
        age = (today - inv.date).days
        i = 0 if age <= 30 else 1 if age <= 60 else 2 if age <= 90 else 3
        key = inv.customer_id or f"x{inv.customer_name}"
        names[key] = (inv.customer_name, inv.customer_mobile)
        buckets[key][i] += inv.due
    for key, b in buckets.items():
        rows.append([names[key][0], names[key][1], *b, sum(b, ZERO)])
    rows.sort(key=lambda r: -r[-1])
    return {
        "columns": [
            ("Customer", "text"),
            ("Mobile", "text"),
            ("0-30 days", "money"),
            ("31-60", "money"),
            ("61-90", "money"),
            ("90+", "money"),
            ("Outstanding", "money"),
        ],
        "rows": rows,
        "totals": ["Total", ""] + [sum((r[i] for r in rows), ZERO) for i in range(2, 7)],
        "chart": ([r[0][:12] for r in rows[:10]], [r[-1] for r in rows[:10]]),
        "notes": ["Unpaid portion of non-cancelled invoices after returns, aged from the invoice date."],
    }


def payables():
    qs = (
        SupplierInvoice.objects.exclude(status="Cancelled")
        .filter(paid_amount__lt=F("total_amount"))
        .values("supplier__name", "supplier__company")
        .annotate(total=Sum("total_amount"), paid=Sum("paid_amount"), n=Count("id"))
        .order_by("-total")
    )
    rows = [
        [r["supplier__name"], r["supplier__company"], r["n"], r["total"], r["paid"], r["total"] - r["paid"]]
        for r in qs
    ]
    return {
        "columns": [
            ("Supplier", "text"),
            ("Company", "text"),
            ("Open invoices", "num"),
            ("Invoiced", "money"),
            ("Paid", "money"),
            ("Payable", "money"),
        ],
        "rows": rows,
        "totals": ["Total", "", sum(r[2] for r in rows)]
        + [sum((r[i] for r in rows), ZERO) for i in (3, 4, 5)],
        "chart": ([r[0][:12] for r in rows[:10]], [r[-1] for r in rows[:10]]),
        "notes": [
            "Recorded supplier invoices not yet fully paid. Receiving goods does not create a payable until the invoice is recorded."
        ],
    }


def returns_report(d_from, d_to):
    qs = (
        SalesReturnLine.objects.filter(
            sales_return__status="approved",
            sales_return__return_date__gte=d_from,
            sales_return__return_date__lte=d_to,
        )
        .values("invoice_line__description", "disposition")
        .annotate(qty=Sum("quantity"), value=Sum("credit_amount"))
        .order_by("-value")
    )
    rows = [
        [
            r["invoice_line__description"],
            dict(SalesReturnLine.Disposition.choices)[r["disposition"]],
            r["qty"],
            r["value"],
        ]
        for r in qs
    ]
    return {
        "columns": [("Product", "text"), ("Condition", "text"), ("Units", "num"), ("Credited", "money")],
        "rows": rows,
        "totals": ["Total", "", sum(r[2] for r in rows), sum((r[3] for r in rows), ZERO)],
        "notes": ["Approved returns only. Damaged units are written off; resellable units go back to stock."],
    }


def purchases(d_from, d_to):
    rows = []
    for r in (
        SupplierInvoice.objects.exclude(status="Cancelled")
        .filter(invoice_date__gte=d_from, invoice_date__lte=d_to)
        .select_related("supplier")
    ):
        rows.append(
            [
                r.invoice_date,
                r.invoice_number,
                r.supplier.name,
                r.total_amount,
                r.paid_amount,
                r.payment_status,
            ]
        )
    pos = (
        PurchaseOrder.objects.filter(order_date__gte=d_from, order_date__lte=d_to)
        .exclude(status="cancelled")
        .count()
    )
    grns = GoodsReceipt.objects.filter(
        status="confirmed", receipt_date__gte=d_from, receipt_date__lte=d_to
    ).count()
    return {
        "columns": [
            ("Date", "date"),
            ("Supplier invoice", "text"),
            ("Supplier", "text"),
            ("Total", "money"),
            ("Paid", "money"),
            ("Payment", "text"),
        ],
        "rows": rows,
        "totals": ["Total", "", "", sum((r[3] for r in rows), ZERO), sum((r[4] for r in rows), ZERO), ""],
        "summary": [("Purchase orders raised", pos), ("Confirmed goods receipts", grns)],
        "notes": ["Purchase cost is shown as supplier invoices; operating expenses are not tracked."],
    }


def inventory_valuation():
    qs = Product.objects.filter(is_active=True, stock_on_hand__gt=0).select_related("category")
    rows = [
        [
            p.sku,
            p.name,
            p.category.name,
            p.stock_on_hand,
            p.cost_price,
            p.stock_on_hand * p.cost_price,
            p.stock_on_hand * p.selling_price,
        ]
        for p in qs.order_by("category__name", "name")
    ]
    cats = defaultdict(lambda: ZERO)
    for r in rows:
        cats[r[2]] += r[5]
    return {
        "columns": [
            ("SKU", "text"),
            ("Product", "text"),
            ("Category", "text"),
            ("On hand", "num"),
            ("Unit cost", "money"),
            ("Value at cost", "money"),
            ("Value at selling price", "money"),
        ],
        "rows": rows,
        "totals": [
            "Total",
            "",
            "",
            sum(r[3] for r in rows),
            "",
            sum((r[5] for r in rows), ZERO),
            sum((r[6] for r in rows), ZERO),
        ],
        "chart": (list(cats.keys())[:12], list(cats.values())[:12]),
        "notes": [
            "Costing method: LATEST PURCHASE COST. Each product is valued at its current cost price, "
            "which is refreshed whenever a goods receipt is confirmed at a different cost. "
            "No FIFO/average layers are kept because the legacy system recorded only one cost per product."
        ],
    }


def low_stock():
    qs = Product.objects.filter(is_active=True, stock_on_hand__lte=F("reorder_level")).select_related(
        "category", "supplier"
    )
    rows = [
        [
            p.sku,
            p.name,
            p.category.name,
            p.stock_on_hand,
            p.reorder_level,
            max(p.reorder_level * 2 - p.stock_on_hand, 0),
            p.supplier.name if p.supplier else "",
            "Out of stock" if p.stock_on_hand <= 0 else "Low",
        ]
        for p in qs.order_by("stock_on_hand", "name")
    ]
    return {
        "columns": [
            ("SKU", "text"),
            ("Product", "text"),
            ("Category", "text"),
            ("On hand", "num"),
            ("Reorder level", "num"),
            ("Suggested order", "num"),
            ("Supplier", "text"),
            ("Status", "status"),
        ],
        "rows": rows,
        "totals": None,
        "notes": [
            "Suggested order = enough to bring stock to twice the reorder level (a simple rule; adjust to your lead times)."
        ],
    }


def stock_ageing(today=None):
    today = today or timezone.localdate()
    inbound = defaultdict(list)
    ins = StockMovement.objects.filter(
        quantity__gt=0,
        movement_type__in=["opening", "purchase", "import", "sale_return", "adjustment", "count"],
    )
    for m in ins.values("product_id", "created_at", "quantity"):
        inbound[m["product_id"]].append((timezone.localtime(m["created_at"]).date(), m["quantity"]))
    rows = []
    labels = None
    for p in (
        Product.objects.filter(is_active=True, stock_on_hand__gt=0)
        .select_related("category")
        .order_by("name")
    ):
        ages = fifo_ageing(p.stock_on_hand, inbound.get(p.pk, []), today)
        labels = list(ages)
        rows.append([p.sku, p.name, *ages.values(), p.stock_on_hand])
    labels = labels or ["0-30 days", "31-60 days", "61-90 days", "90+ days"]
    return {
        "columns": [("SKU", "text"), ("Product", "text")]
        + [(lbl, "num") for lbl in labels]
        + [("Total", "num")],
        "rows": rows,
        "totals": ["Total", ""] + [sum(r[i] for r in rows) for i in range(2, 2 + len(labels) + 1)],
        "notes": [
            "FIFO estimate: the newest receipts are assumed to be what is left on the shelf. "
            "Opening stock migrated from the legacy system is dated at cut-over, so it ages from that date."
        ],
    }


def reconciliation():
    """Cross-checks that should always hold; any row listed is a data problem to investigate."""
    rows = []
    for inv in Invoice.objects.exclude(status="Cancelled").annotate(
        paid=Coalesce(
            Sum("payments__amount", filter=Q(payments__status="completed", payments__kind="payment")), ZERO
        ),
        refunded=Coalesce(
            Sum("payments__amount", filter=Q(payments__status="completed", payments__kind="refund")), ZERO
        ),
    ):
        if inv.paid - inv.refunded != inv.total_paid:
            rows.append(
                [
                    "Invoice paid mismatch",
                    inv.number,
                    f"total_paid {inv.total_paid} vs payment rows {inv.paid - inv.refunded}",
                ]
            )
        if inv.total_paid > inv.effective_total:
            rows.append(
                [
                    "Overpaid invoice",
                    inv.number,
                    f"received {inv.total_paid} vs payable {inv.effective_total}",
                ]
            )
    from apps.inventory.services import reconcile as stock_reconcile

    for p, ledger, onhand in stock_reconcile():
        rows.append(["Stock ledger mismatch", p.sku, f"ledger {ledger} vs on hand {onhand}"])
    return {
        "columns": [("Check", "text"), ("Reference", "text"), ("Detail", "text")],
        "rows": rows,
        "totals": None,
        "notes": [
            "An empty report means invoices, payments and the stock ledger reconcile. Migrated legacy "
            "invoices that were overpaid or had inconsistent totals appear here for manual review."
        ],
    }


# ---- dashboard -------------------------------------------------------------
def dashboard_data(today=None):
    today = today or timezone.localdate()
    month_start = today.replace(day=1)
    week_start = today - timedelta(days=6)

    def invoiced(d1, d2=None):
        return live_invoices(d1, d2 or today).aggregate(t=Sum("total"), n=Count("id"))

    day, week, month = invoiced(today), invoiced(week_start), invoiced(month_start)
    collected = Payment.objects.filter(
        status="completed", paid_on__gte=month_start, paid_on__lte=today
    ).aggregate(p=Sum("amount", filter=Q(kind="payment")), r=Sum("amount", filter=Q(kind="refund")))
    collected_today = Payment.objects.filter(status="completed", paid_on=today).aggregate(
        p=Sum("amount", filter=Q(kind="payment")), r=Sum("amount", filter=Q(kind="refund"))
    )
    due = Invoice.objects.exclude(status="Cancelled").aggregate(
        t=Sum("total"), r=Sum("returned_total"), p=Sum("total_paid")
    )
    trend = {
        r["date"]: r["t"]
        for r in live_invoices(today - timedelta(days=13), today).values("date").annotate(t=Sum("total"))
    }
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    since30 = today - timedelta(days=29)
    top_products = list(
        InvoiceLine.objects.filter(invoice__date__gte=since30)
        .exclude(invoice__status="Cancelled")
        .values_list("description")
        .annotate(q=Sum("quantity"))
        .order_by("-q")[:5]
    )
    top_customers = list(
        live_invoices(since30, today).values_list("customer_name").annotate(s=Sum("total")).order_by("-s")[:5]
    )
    methods = list(
        Payment.objects.filter(status="completed", kind="payment", paid_on__gte=month_start)
        .values_list("method")
        .annotate(s=Sum("amount"))
        .order_by("-s")
    )
    labels = dict(Payment.Method.choices)
    active = Product.objects.filter(is_active=True)
    return {
        "sales_today": day["t"] or ZERO,
        "orders_today": day["n"],
        "sales_week": week["t"] or ZERO,
        "sales_month": month["t"] or ZERO,
        "orders_month": month["n"],
        "avg_order": ((month["t"] or ZERO) / month["n"]).quantize(Decimal("0.01")) if month["n"] else ZERO,
        "active_customers": live_invoices(since30, today)
        .exclude(customer__isnull=True)
        .values("customer")
        .distinct()
        .count(),
        "total_customers": Customer.objects.filter(is_active=True).count(),
        "collected_today": (collected_today["p"] or ZERO) - (collected_today["r"] or ZERO),
        "collected_month": (collected["p"] or ZERO) - (collected["r"] or ZERO),
        "receivable": (due["t"] or ZERO) - (due["r"] or ZERO) - (due["p"] or ZERO),
        "unpaid_invoices": Invoice.objects.filter(status="Pending").count(),
        "trend_labels": [d.strftime("%d %b") for d in days],
        "trend_values": [trend.get(d, ZERO) for d in days],
        "top_products": top_products,
        "top_customers": top_customers,
        "methods": [(labels.get(m, m), s) for m, s in methods],
        "low_stock": list(
            active.filter(stock_on_hand__lte=F("reorder_level"), stock_on_hand__gt=0).order_by(
                "stock_on_hand"
            )[:8]
        ),
        "n_low": active.filter(stock_on_hand__lte=F("reorder_level"), stock_on_hand__gt=0).count(),
        "n_out": active.filter(stock_on_hand__lte=0).count(),
        "pending_returns": SalesReturn.objects.filter(status="pending").count(),
    }
