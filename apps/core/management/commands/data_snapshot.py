"""Print a JSON snapshot of record counts, financial totals and integrity checks.

Used by scripts/backup.sh (written next to every backup) and scripts/verify_restore.sh (compared against a
restored copy) so that "the backup file exists" is never mistaken for "the backup works".
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.models import F, Sum

from apps.customers.models import Customer, LedgerEntry
from apps.inventory.models import StockMovement
from apps.inventory.services import reconcile
from apps.payments.models import Payment
from apps.products.models import Product
from apps.purchasing.models import GoodsReceipt, PurchaseOrder, SupplierInvoice
from apps.returns.models import SalesReturn
from apps.sales.models import Invoice, InvoiceLine
from apps.suppliers.models import Supplier


def _sum(qs, field):
    return str(qs.aggregate(s=Sum(field))["s"] or 0)


def snapshot():
    counts = {
        "suppliers": Supplier.objects.count(),
        "products": Product.objects.count(),
        "customers": Customer.objects.count(),
        "invoices": Invoice.objects.count(),
        "invoice_lines": InvoiceLine.objects.count(),
        "payments": Payment.objects.count(),
        "ledger_entries": LedgerEntry.objects.count(),
        "stock_movements": StockMovement.objects.count(),
        "purchase_orders": PurchaseOrder.objects.count(),
        "goods_receipts": GoodsReceipt.objects.count(),
        "supplier_invoices": SupplierInvoice.objects.count(),
        "returns": SalesReturn.objects.count(),
    }
    totals = {
        "invoice_total": _sum(Invoice.objects.exclude(status="Cancelled"), "total"),
        "payments_completed": _sum(Payment.objects.filter(status="completed", kind="payment"), "amount"),
        "refunds_completed": _sum(Payment.objects.filter(status="completed", kind="refund"), "amount"),
        "stock_units": _sum(Product.objects.all(), "stock_on_hand"),
        "stock_ledger_units": _sum(StockMovement.objects.all(), "quantity"),
        "ledger_debits": _sum(LedgerEntry.objects.filter(entry_type="Debit"), "amount"),
    }
    media = Path(settings.MEDIA_ROOT)
    missing = [p.image.name for p in Product.objects.exclude(image="") if not (media / p.image.name).exists()]
    with (
        connection.cursor() as cur
    ):  # every FK must resolve (the DB enforces it; this proves the restore kept it)
        cur.execute(
            "SELECT count(*) FROM sales_invoiceline l LEFT JOIN sales_invoice i ON i.id=l.invoice_id "
            "WHERE i.id IS NULL"
        )
        orphan_lines = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM payments_payment p LEFT JOIN sales_invoice i ON i.id=p.invoice_id "
            "WHERE i.id IS NULL"
        )
        orphan_payments = cur.fetchone()[0]
    checks = {
        "stock_ledger_mismatches": len(reconcile()),
        "orphan_invoice_lines": orphan_lines,
        "orphan_payments": orphan_payments,
        "invoice_equation_violations": Invoice.objects.exclude(
            total=F("subtotal") - F("discount_amount") + F("tax_total") + F("shipping_charge")
        ).count(),
        "missing_media_files": missing,
    }
    return {"counts": counts, "totals": totals, "checks": checks}


class Command(BaseCommand):
    help = "Print a JSON snapshot of counts, financial totals and integrity checks."

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(snapshot(), indent=2, sort_keys=True))
