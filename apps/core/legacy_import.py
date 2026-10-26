"""Copy the legacy Flask database into the Django schema, preserving primary keys.

Design (see docs/MIGRATION.md):

* The legacy database is opened READ ONLY through the ``legacy`` connection alias.
  Legacy tables are never altered, so the Flask app stays a working rollback.
* Everything is written to the Django tables in ONE transaction: any failure
  rolls the whole import back.
* Primary keys are preserved (``pro_id`` -> ``Product.id``, ``invoice_no`` ->
  ``Invoice.id``/``number`` ...) and sequences are advanced afterwards.
* Rows that would violate a Django constraint are never "fixed" silently: they are
  listed in the quarantine report and the import aborts unless
  ``allow_quarantine`` is set.
* Stock is carried over as ``stock_on_hand`` plus one OPENING movement, so the
  ledger reconciles from day one. Historical documents do not create movements.
* Legacy payment rows with status ``Pending`` (placeholders created by
  ``DB/update.sql`` for unpaid invoices) are imported as PENDING and never counted
  as money received.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.core.management.color import no_style
from django.db import connection, connections, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.models import DocumentSequence
from apps.core.money import ZERO, q2
from apps.customers.models import Customer, LedgerEntry
from apps.inventory.models import StockMovement
from apps.payments.models import Payment
from apps.products.models import Category, Product
from apps.purchasing.models import (GoodsReceipt, GoodsReceiptLine, SupplierInvoice,
                                    SupplierInvoiceLine, SupplierPayment)
from apps.sales.calculations import LineInput, compute_invoice
from apps.sales.models import Invoice, InvoiceLine
from apps.suppliers.models import Supplier

METHOD_MAP = {"cash": "cash", "credit_card": "credit_card", "card": "credit_card",
              "bank_transfer": "bank_transfer", "upi": "upi"}
INVOICE_STATUS = {"completed": Invoice.Status.COMPLETED, "pending": Invoice.Status.PENDING}
PAYMENT_STATUS = {"paid": Invoice.PaymentStatus.PAID, "unpaid": Invoice.PaymentStatus.UNPAID,
                  "partially paid": Invoice.PaymentStatus.PARTIAL}


@dataclass
class Report:
    counts_source: dict = field(default_factory=dict)
    counts_target: dict = field(default_factory=dict)
    totals: dict = field(default_factory=dict)
    anomalies: list = field(default_factory=list)
    quarantine: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def anomaly(self, kind, ref, detail):
        self.anomalies.append({"kind": kind, "ref": str(ref), "detail": detail})

    def quarantined(self, table, key, reason):
        self.quarantine.append({"table": table, "key": str(key), "reason": reason})

    def as_dict(self):
        return {"counts_source": self.counts_source, "counts_target": self.counts_target,
                "totals": self.totals, "anomalies": self.anomalies, "quarantine": self.quarantine,
                "notes": self.notes}


class LegacyImportError(Exception):
    pass


def _rows(cursor, sql):
    cursor.execute(sql)
    cols = [c.name for c in cursor.description]
    return [dict(zip(cols, r, strict=True)) for r in cursor.fetchall()]


def _aware(value):
    """Legacy timestamps are naive; interpret them in the configured TIME_ZONE."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    return timezone.make_aware(datetime.combine(value, datetime.min.time()))


def _dec(v, default=ZERO):
    return q2(v) if v is not None else default


def _has_column(cur, table, column):
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name=%s AND column_name=%s",
                (table, column))
    return cur.fetchone() is not None


def _has_table(cur, table):
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_name=%s", (table,))
    return cur.fetchone() is not None


def run(*, source="legacy", dry_run=False, allow_quarantine=False, report_path=None) -> Report:
    if source not in connections.databases:
        raise LegacyImportError(
            f"Database alias '{source}' is not configured. Set LEGACY_DATABASE_URL.")
    if Customer.objects.exists() or Product.objects.exists() or Invoice.objects.exists():
        raise LegacyImportError("The target database already contains business data. The importer "
                                "refuses to merge into a non-empty target; restore a clean database.")
    rep = Report()
    src = connections[source]
    with src.cursor() as cur:
        data = _extract(cur, rep)
    _validate(data, rep)
    if rep.quarantine and not allow_quarantine:
        _finish(rep, report_path)
        raise LegacyImportError(
            f"{len(rep.quarantine)} legacy row(s) cannot satisfy the new integrity constraints. "
            "Review the report; re-run with --allow-quarantine to import everything else.")
    try:
        with transaction.atomic():
            _load(data, rep)
            _verify(data, rep)
            if dry_run:
                raise _DryRun()
    except _DryRun:
        rep.notes.append("DRY RUN: everything was validated and loaded, then rolled back.")
    _finish(rep, report_path)
    return rep


class _DryRun(Exception):
    pass


def _finish(rep, path):
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(rep.as_dict(), fh, indent=2, default=str)


# --------------------------------------------------------------------------
def _extract(cur, rep):
    d = {}
    d["suppliers"] = _rows(cur, "SELECT * FROM supplier ORDER BY id")
    d["products"] = _rows(cur, """
        SELECT p.pro_id, p.pro_name, p.cost_price, p.selling_price, p.quantity AS ap_quantity,
               p.unit, p.category, p.safety_level, p.expiry_date, p.supplier_id,
               p.terms_from_invoice, p.hc_code,
               i.product_id IS NOT NULL AS has_inv, i.quantity AS inv_quantity,
               i.category AS inv_category, i.hscode AS inv_hscode
        FROM add_product p LEFT JOIN inventory i ON i.product_id = p.pro_id ORDER BY p.pro_id""")
    d["customers"] = _rows(cur, "SELECT * FROM customer ORDER BY id")
    pm = ", payment_method" if _has_column(cur, "invoice", "payment_method") else ""
    d["invoices"] = _rows(cur, f"""
        SELECT invoice_no, customer_id, customer_name, customer_mobile, date, terms, sale_by,
               total_amount, discount, final_amount, status, payment_status, total_paid {pm}
        FROM invoice ORDER BY invoice_no""")
    d["lines"] = _rows(cur, """
        SELECT ip.id, ip.invoice_no, ip.product_id, ip.quantity, ip.unit, ip.price, ip.discount, ip.total
        FROM invoice_products ip ORDER BY ip.id""")
    d["payments"] = _rows(cur, "SELECT * FROM payment ORDER BY id")
    d["ledger"] = _rows(cur, "SELECT * FROM ledger ORDER BY id")
    d["add_items"] = _rows(cur, "SELECT * FROM add_item ORDER BY item_id")
    d["supplier_invoices"] = _rows(cur, "SELECT * FROM supplier_invoice ORDER BY invoice_id")
    for k, v in d.items():
        rep.counts_source[k] = len(v)
    cur.execute("SELECT COALESCE(SUM(final_amount),0) FROM invoice")
    rep.totals["source_invoice_total"] = str(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(amount),0) FROM payment "
                "WHERE lower(COALESCE(status,'')) IN ('paid','completed')")
    rep.totals["source_payments_completed"] = str(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(quantity),0) FROM inventory")
    rep.totals["source_stock_units"] = str(cur.fetchone()[0])
    cur.execute("SELECT COALESCE(SUM(i.quantity * p.cost_price),0) FROM inventory i "
                "JOIN add_product p ON p.pro_id=i.product_id")
    rep.totals["source_stock_value_at_cost"] = str(cur.fetchone()[0])
    d["has_sale_product"] = _has_table(cur, "sale_product")
    if d["has_sale_product"]:
        rep.notes.append("Table sale_product exists in the legacy DB; it is not part of table.sql "
                         "and is NOT imported (invoice_products is the line source).")
    return d


def _validate(d, rep):
    """Pre-flight: find rows the new constraints would reject."""
    bad_invoices = set()
    for inv in d["invoices"]:
        total_amount, disc = _dec(inv["total_amount"]), _dec(inv["discount"])
        final = _dec(inv["final_amount"])
        if disc < 0 or disc > total_amount:
            rep.quarantined("invoice", inv["invoice_no"], f"discount {disc} outside 0..{total_amount}")
            bad_invoices.add(inv["invoice_no"])
        elif total_amount < 0 or final < 0:
            rep.quarantined("invoice", inv["invoice_no"], "negative amount")
            bad_invoices.add(inv["invoice_no"])
        if inv["total_paid"] is not None and _dec(inv["total_paid"]) < 0:
            rep.quarantined("invoice", inv["invoice_no"], "negative total_paid")
            bad_invoices.add(inv["invoice_no"])
    for ln in d["lines"]:
        if ln["quantity"] is None or ln["quantity"] <= 0:
            rep.quarantined("invoice_products", ln["id"], f"quantity {ln['quantity']} <= 0")
            bad_invoices.add(ln["invoice_no"])
        elif _dec(ln["price"]) * ln["quantity"] < _dec(ln["discount"]):
            rep.quarantined("invoice_products", ln["id"], "line discount exceeds line amount")
            bad_invoices.add(ln["invoice_no"])
    d["bad_invoices"] = bad_invoices
    for inv in d["invoices"]:
        if inv["invoice_no"] in bad_invoices and not any(
                q["table"] == "invoice" and q["key"] == str(inv["invoice_no"]) for q in rep.quarantine):
            rep.quarantined("invoice", inv["invoice_no"], "has an invalid line (see invoice_products)")
    for p in d["products"]:
        if _dec(p["cost_price"]) < 0 or _dec(p["selling_price"]) < 0:
            rep.quarantined("add_product", p["pro_id"], "negative price")
    d["bad_products"] = {q["key"] for q in rep.quarantine if q["table"] == "add_product"}
    for ln in d["lines"]:
        if str(ln["product_id"]) in d["bad_products"]:
            d["bad_invoices"].add(ln["invoice_no"])


def _load(d, rep):
    tz_now = timezone.now()
    # --- suppliers --------------------------------------------------------
    suppliers = [Supplier(id=s["id"], name=s["name"], company=s.get("company") or "",
                          mobile=s.get("mobile") or "", email=s.get("mail") or "",
                          terms=s.get("terms") or "") for s in d["suppliers"]]
    Supplier.objects.bulk_create(suppliers)
    for s in d["suppliers"]:
        if s.get("timestamp"):
            Supplier.objects.filter(pk=s["id"]).update(created_at=_aware(s["timestamp"]))
    supplier_ids = {s["id"] for s in d["suppliers"]}

    # --- categories & products -------------------------------------------
    cats = {}

    def category_for(name):
        name = (name or "General").strip() or "General"
        key = name.lower()
        if key not in cats:
            cats[key] = Category.objects.create(name=name)
        return cats[key]

    products, opening = [], []
    for p in d["products"]:
        if str(p["pro_id"]) in d["bad_products"]:
            continue
        stock = p["inv_quantity"] if p["has_inv"] and p["inv_quantity"] is not None else p["ap_quantity"]
        stock = stock or 0
        if p["has_inv"] and p["inv_quantity"] != p["ap_quantity"]:
            rep.anomaly("stock_mismatch", p["pro_id"],
                        f"inventory.quantity={p['inv_quantity']} but add_product.quantity="
                        f"{p['ap_quantity']}; inventory.quantity used")
        if not p["has_inv"]:
            rep.anomaly("no_inventory_row", p["pro_id"], "no inventory row; add_product.quantity used")
        reorder = 0
        raw = p["safety_level"]
        try:
            reorder = max(int(str(raw)), 0) if raw not in (None, "") else 0
        except ValueError:
            rep.anomaly("bad_safety_level", p["pro_id"], f"non-numeric safety_level {raw!r} -> 0")
        sup = p["supplier_id"] if p["supplier_id"] in supplier_ids else None
        if p["supplier_id"] and sup is None:
            rep.anomaly("orphan_supplier", p["pro_id"], f"supplier {p['supplier_id']} missing")
        if stock < 0:
            rep.anomaly("negative_stock", p["pro_id"], f"stock {stock}; allow_negative_stock enabled")
        products.append(Product(
            id=p["pro_id"], sku=str(p["pro_id"]), name=p["pro_name"],
            category=category_for(p["category"] or p["inv_category"]), unit=p["unit"] or "pcs",
            hs_code=p["hc_code"] or p["inv_hscode"] or "", supplier_id=sup,
            supplier_terms=p["terms_from_invoice"] or "", cost_price=_dec(p["cost_price"]),
            selling_price=_dec(p["selling_price"]), tax_rate=ZERO, stock_on_hand=stock,
            reorder_level=reorder, allow_negative_stock=stock < 0, expiry_date=p["expiry_date"]))
        if stock:
            opening.append((p["pro_id"], stock, _dec(p["cost_price"])))
    Product.objects.bulk_create(products)
    StockMovement.objects.bulk_create([
        StockMovement(product_id=pid, movement_type=StockMovement.Type.OPENING, quantity=qty,
                      balance_after=qty, unit_cost=cost, reference_type="legacy_import",
                      note="Opening balance carried over from the legacy inventory table",
                      created_at=tz_now) for pid, qty, cost in opening])
    cost_by_product = {p.id: p.cost_price for p in products}
    name_by_product = {p.id: p.name for p in products}

    # --- customers -------------------------------------------------------
    Customer.objects.bulk_create([
        Customer(id=c["id"], name=c["name"], mobile=c["mobile"], billing_address=c.get("address") or "",
                 email=c.get("mail") or "") for c in d["customers"]])
    for c in d["customers"]:
        if c.get("time"):
            Customer.objects.filter(pk=c["id"]).update(created_at=_aware(c["time"]))
    customer_ids = {c["id"] for c in d["customers"]}
    customer_by_mobile = {c["mobile"]: c["id"] for c in d["customers"]}

    # --- invoices & lines -------------------------------------------------
    lines_by_invoice = defaultdict(list)
    for ln in d["lines"]:
        lines_by_invoice[ln["invoice_no"]].append(ln)
    good_invoices = [i for i in d["invoices"] if i["invoice_no"] not in d["bad_invoices"]]
    invoices, line_objs = [], []
    for inv in good_invoices:
        no = inv["invoice_no"]
        total_amount, disc, final = _dec(inv["total_amount"]), _dec(inv["discount"]), _dec(inv["final_amount"])
        cust = inv["customer_id"] if inv["customer_id"] in customer_ids else None
        if inv["customer_id"] and cust is None:
            rep.anomaly("orphan_customer", no, f"customer {inv['customer_id']} missing")
        status = INVOICE_STATUS.get((inv["status"] or "").lower())
        if status is None:
            rep.anomaly("unknown_invoice_status", no, f"{inv['status']!r} -> Pending")
            status = Invoice.Status.PENDING
        pstatus = PAYMENT_STATUS.get((inv["payment_status"] or "").lower())
        if pstatus is None:
            rep.anomaly("unknown_payment_status", no, f"{inv['payment_status']!r} -> Unpaid")
            pstatus = Invoice.PaymentStatus.UNPAID
        pct = (disc / total_amount * 100).quantize(Decimal("0.01")) if total_amount else ZERO
        invoices.append(Invoice(
            id=no, number=str(no), customer_id=cust, customer_name=inv["customer_name"],
            customer_mobile=inv["customer_mobile"], date=inv["date"], terms=inv["terms"] or "",
            sold_by=inv["sale_by"] or "", subtotal=total_amount, discount_percent=min(pct, Decimal("100")),
            discount_amount=disc, shipping_charge=ZERO, tax_total=ZERO, total=total_amount - disc,
            total_paid=ZERO, status=status, payment_status=pstatus, is_legacy=True))
        if final != total_amount - disc:
            rep.anomaly("final_amount_mismatch", no, f"final_amount {final} != total-discount")
        lines = lines_by_invoice.get(no, [])
        if not lines:
            rep.anomaly("invoice_without_lines", no, "no invoice_products rows")
        nets = [(_dec(ln["price"]) * ln["quantity"] - _dec(ln["discount"])) for ln in lines]
        allocations = [ZERO] * len(lines)
        if lines and sum(nets, ZERO) == total_amount:
            res = compute_invoice([LineInput(ln["quantity"], _dec(ln["price"]), _dec(ln["discount"]))
                                   for ln in lines], discount_amount=disc)
            allocations = [r.allocated_discount for r in res.lines]
        elif lines:
            rep.anomaly("lines_do_not_sum", no,
                        f"sum of line nets {sum(nets, ZERO)} != invoice total_amount {total_amount}")
        for ln, net, alloc in zip(lines, nets, allocations, strict=True):
            if ln["product_id"] not in cost_by_product:
                rep.anomaly("orphan_product", f"line {ln['id']}", f"product {ln['product_id']} missing")
                continue
            line_objs.append(InvoiceLine(
                id=ln["id"], invoice_id=no, product_id=ln["product_id"],
                description=name_by_product[ln["product_id"]], unit=ln["unit"] or "",
                quantity=ln["quantity"], unit_price=_dec(ln["price"]), discount=_dec(ln["discount"]),
                net_amount=net, allocated_discount=alloc, taxable_amount=net - alloc,
                tax_rate=ZERO, tax_amount=ZERO, line_total=net - alloc,
                unit_cost=cost_by_product[ln["product_id"]]))
    Invoice.objects.bulk_create(invoices)
    InvoiceLine.objects.bulk_create(line_objs)
    for inv in good_invoices:
        Invoice.objects.filter(pk=inv["invoice_no"]).update(created_at=_aware(inv["date"]))

    # --- payments (+ reconciling rows) -----------------------------------
    valid_invoice_ids = {i["invoice_no"] for i in good_invoices}
    completed_by_invoice = defaultdict(lambda: ZERO)
    pay_objs = []
    for p in d["payments"]:
        if p["invoice_no"] not in valid_invoice_ids:
            rep.anomaly("payment_for_quarantined_invoice", p["id"], f"invoice {p['invoice_no']}")
            continue
        raw_status = (p["status"] or "").lower()
        if raw_status in ("paid", "completed"):
            status = Payment.Status.COMPLETED
        else:
            status = Payment.Status.PENDING
            if raw_status != "pending":
                rep.anomaly("unknown_payment_row_status", p["id"], f"{p['status']!r} -> pending")
        mode = (p["payment_mode"] or "").strip()
        method = METHOD_MAP.get(mode.lower(), "other")
        if mode.lower() not in METHOD_MAP and mode.lower() != "pending":
            rep.anomaly("unknown_payment_mode", p["id"], f"{mode!r} -> other")
        amount = _dec(p["amount"])
        if status == Payment.Status.COMPLETED:
            completed_by_invoice[p["invoice_no"]] += amount
        pay_objs.append(Payment(
            id=p["id"], invoice_id=p["invoice_no"], customer_id=p.get("customer_id") if p.get("customer_id") in customer_ids else None,
            kind=Payment.Kind.PAYMENT, amount=amount, method=method, legacy_mode=mode,
            reference=(p.get("payment_receipt") or "")[:100], receipt_no=(p.get("receipt_no") or "")[:100],
            paid_on=p["invoice_date"], status=status,
            notes="Imported from legacy" + (" (placeholder, not money received)" if status == Payment.Status.PENDING else "")))
    Payment.objects.bulk_create(pay_objs)
    next_pay_id = max([p["id"] for p in d["payments"]] or [0]) + 1
    recon = []
    for inv in good_invoices:
        no = inv["invoice_no"]
        legacy_paid = _dec(inv["total_paid"])
        rows_paid = completed_by_invoice[no]
        final = _dec(inv["final_amount"])
        if legacy_paid > rows_paid:
            diff = legacy_paid - rows_paid
            recon.append(Payment(
                id=next_pay_id, invoice_id=no, customer_id=inv["customer_id"] if inv["customer_id"] in customer_ids else None,
                kind=Payment.Kind.PAYMENT, amount=diff, method="other", legacy_mode="reconciliation",
                paid_on=inv["date"], status=Payment.Status.COMPLETED,
                notes="Reconciling entry created at legacy import: invoice.total_paid exceeded recorded payments"))
            next_pay_id += 1
            completed_by_invoice[no] = legacy_paid
            rep.anomaly("paid_without_payment_rows", no,
                        f"invoice.total_paid {legacy_paid} vs payment rows {rows_paid}; "
                        f"reconciling payment of {diff} created")
        elif rows_paid > legacy_paid:
            rep.anomaly("payment_rows_exceed_total_paid", no,
                        f"payment rows {rows_paid} vs invoice.total_paid {legacy_paid}; payment rows used")
        if completed_by_invoice[no] > final:
            rep.anomaly("overpaid_invoice", no,
                        f"received {completed_by_invoice[no]} exceeds invoice total {final} - review manually")
    Payment.objects.bulk_create(recon)
    for inv in good_invoices:
        Invoice.objects.filter(pk=inv["invoice_no"]).update(total_paid=completed_by_invoice[inv["invoice_no"]])

    # --- ledger ----------------------------------------------------------
    inv_ref = re.compile(r"Invoice #(\d+)")
    led = []
    for e in d["ledger"]:
        cid = customer_by_mobile.get(e["customer_mobile"]) or (e["customer_id"] if e["customer_id"] in customer_ids else None)
        if cid is None:
            rep.anomaly("ledger_without_customer", e["id"], f"mobile {e['customer_mobile']}")
            continue
        m = inv_ref.search(e["description"] or "")
        linked = int(m.group(1)) if m and int(m.group(1)) in valid_invoice_ids else None
        led.append(LedgerEntry(
            id=e["id"], customer_id=cid, entry_date=_aware(e["transaction_date"]),
            entry_type=e["transaction_type"], amount=_dec(e["amount"]), balance=_dec(e["balance"]),
            description=e["description"] or "", invoice_id=linked, source=LedgerEntry.Source.LEGACY))
    LedgerEntry.objects.bulk_create(led)

    # --- purchasing ------------------------------------------------------
    receipts, rlines = [], []
    receipt_by_invoice_no = {}
    for it in d["add_items"]:
        if it["product_id"] not in cost_by_product:
            rep.anomaly("add_item_orphan_product", it["item_id"], f"product {it['product_id']} missing")
            continue
        sup = it["supplier_id"] if it["supplier_id"] in supplier_ids else None
        if sup is None:
            rep.anomaly("add_item_without_supplier", it["item_id"], "supplier missing; row skipped")
            continue
        r = GoodsReceipt(number=f"LEGACY-GRN-{it['item_id']}", supplier_id=sup,
                         receipt_date=_aware(it["added_date"]).date(), delivery_note=it["invoice_no"] or "",
                         status=GoodsReceipt.Status.CONFIRMED, confirmed_at=_aware(it["added_date"]),
                         comments=it["comments"] or "", legacy_item_id=it["item_id"])
        receipts.append((r, it))
    for r, _ in receipts:
        r.save()
        GoodsReceipt.objects.filter(pk=r.pk).update(created_at=r.confirmed_at)
    for r, it in receipts:
        rlines.append(GoodsReceiptLine(receipt=r, product_id=it["product_id"], quantity=it["quantity_added"],
                                       unit_cost=cost_by_product[it["product_id"]]))
        if it["invoice_no"]:
            receipt_by_invoice_no[it["invoice_no"]] = r
    GoodsReceiptLine.objects.bulk_create(rlines)

    max_year_seq = defaultdict(int)
    for si in d["supplier_invoices"]:
        sup = si["supplier_id"] if si["supplier_id"] in supplier_ids else None
        if sup is None or si["product_id"] not in cost_by_product:
            rep.anomaly("supplier_invoice_skipped", si["invoice_id"], "supplier or product missing")
            continue
        total = _dec(si["total_amount"])
        paid = total if (si["payment_status"] or "").lower() == "paid" else ZERO
        status = {"pending": "Pending", "confirmed": "Confirmed", "cancelled": "Cancelled"}.get(
            (si["status"] or "").lower(), "Pending")
        obj = SupplierInvoice.objects.create(
            id=si["invoice_id"], invoice_number=si["invoice_no"], supplier_id=sup,
            receipt=receipt_by_invoice_no.get(si["invoice_no"]),
            invoice_date=_aware(si["invoice_date"]).date(), total_amount=total, paid_amount=paid,
            status=status, payment_status=SupplierInvoice.PaymentStatus.PAID if paid else
            SupplierInvoice.PaymentStatus.UNPAID)
        SupplierInvoiceLine.objects.create(invoice=obj, product_id=si["product_id"], quantity=si["quantity"],
                                           unit_cost=q2(total / si["quantity"]))
        if paid:
            SupplierPayment.objects.create(
                invoice=obj, amount=paid, method="other", paid_on=obj.invoice_date,
                reference="legacy import", idempotency_key=f"legacy-supplier-invoice-{obj.pk}")
        m = re.fullmatch(r"(\d{4})(\d{5})", si["invoice_no"] or "")
        if m:
            max_year_seq[int(m.group(1))] = max(max_year_seq[int(m.group(1))], int(m.group(2)))

    # --- sequences --------------------------------------------------------
    max_no = max([i["invoice_no"] for i in d["invoices"]] or [settings.INVOICE_NUMBER_START - 1])
    DocumentSequence.ensure_at_least("invoice", max(max_no, settings.INVOICE_NUMBER_START - 1))
    for year, n in max_year_seq.items():
        DocumentSequence.ensure_at_least(f"supplier_invoice-{year}", n)
    models = [Supplier, Product, Customer, Invoice, InvoiceLine, Payment, LedgerEntry,
              SupplierInvoice, Category]
    with connection.cursor() as cur:
        for sql in connection.ops.sequence_reset_sql(no_style(), models):
            cur.execute(sql)


def _verify(d, rep):
    rep.counts_target = {
        "suppliers": Supplier.objects.count(), "products": Product.objects.count(),
        "customers": Customer.objects.count(), "invoices": Invoice.objects.count(),
        "lines": InvoiceLine.objects.count(), "payments": Payment.objects.count(),
        "ledger": LedgerEntry.objects.count(), "add_items": GoodsReceipt.objects.count(),
        "supplier_invoices": SupplierInvoice.objects.count()}
    agg = lambda qs, f: str(qs.aggregate(s=Sum(f))["s"] or 0)  # noqa: E731
    rep.totals["target_invoice_total"] = agg(Invoice.objects.all(), "total")
    rep.totals["target_payments_completed"] = agg(
        Payment.objects.filter(status="completed").exclude(legacy_mode="reconciliation"), "amount")
    rep.totals["target_stock_units"] = agg(Product.objects.all(), "stock_on_hand")
    rep.totals["target_stock_value_at_cost"] = str(sum(p.cost_price * p.stock_on_hand for p in Product.objects.all()))
    rep.totals["target_stock_ledger_units"] = agg(StockMovement.objects.all(), "quantity")
    q = {t["table"]: 0 for t in rep.quarantine}
    for t in rep.quarantine:
        q[t["table"]] += 1
    mismatches = []
    for src_key, tgt_key, tbl in (("suppliers", "suppliers", None), ("products", "products", "add_product"),
                                  ("customers", "customers", None), ("invoices", "invoices", "invoice"),
                                  ("payments", "payments", None)):
        expected = rep.counts_source[src_key] - q.get(tbl, 0) if tbl else rep.counts_source[src_key]
        got = rep.counts_target[tgt_key]
        if tgt_key == "payments":
            got -= Payment.objects.filter(legacy_mode="reconciliation").count()
            expected -= sum(1 for a in rep.anomalies if a["kind"] == "payment_for_quarantined_invoice")
        if expected != got:
            mismatches.append(f"{src_key}: source {expected} vs target {got}")
    if rep.totals["source_stock_units"] != rep.totals["target_stock_units"] and not rep.quarantine:
        mismatches.append(f"stock units: {rep.totals['source_stock_units']} vs {rep.totals['target_stock_units']}")
    if rep.totals["target_stock_units"] != rep.totals["target_stock_ledger_units"]:
        mismatches.append("stock_on_hand does not equal the sum of the stock ledger")
    if not rep.quarantine and rep.totals["source_invoice_total"] != rep.totals["target_invoice_total"]:
        mismatches.append(f"invoice totals: {rep.totals['source_invoice_total']} vs {rep.totals['target_invoice_total']}")
    rep.totals["verification_mismatches"] = mismatches
    if mismatches:
        raise LegacyImportError("Post-import verification failed: " + "; ".join(mismatches))
