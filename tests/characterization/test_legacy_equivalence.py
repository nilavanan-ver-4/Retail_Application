"""Compare the Django implementation with the ORIGINAL Flask app's recorded behaviour.

``legacy_run`` executes the unmodified legacy code against a real PostgreSQL database built from
legacy_flask/DB/*.sql, so these are true characterization tests, not assumptions.
"""

import json
from decimal import Decimal as D
from pathlib import Path

import pytest

from apps.core import legacy_import
from apps.customers.models import Customer, LedgerEntry
from apps.inventory.models import StockMovement
from apps.inventory.services import reconcile
from apps.payments.models import Payment
from apps.products.models import Product
from apps.purchasing.models import SupplierInvoice
from apps.purchasing.services import quick_receive
from apps.sales.models import Invoice
from apps.sales.services import create_invoice

pytestmark = [pytest.mark.characterization, pytest.mark.django_db(transaction=True)]
GOLDEN = json.loads((Path(__file__).parent / "legacy_golden.json").read_text())


def test_harness_still_reproduces_recorded_golden(legacy_run):
    """Guards the baseline: re-running the legacy code gives the recorded observations."""
    _, observed = legacy_run
    for key in ("invoice", "invoice_products", "payment", "ledger", "inventory", "add_product_qty"):
        assert observed[key] == GOLDEN[key], key
    assert observed["supplier_invoice"][0][1:] == GOLDEN["supplier_invoice"][0][1:]


def test_legacy_defects_are_real_and_documented(legacy_run):
    """The behaviours the Django version intentionally changes (see docs/MIGRATION.md 'intentional changes')."""
    _, o = legacy_run
    assert o["inventory_after_add"][0][1] == 200  # product created with 100 -> stock doubled by two triggers
    assert o["invoice4_final_stored"][0][1:] == [
        "10.00",
        "10.00",
    ]  # 70.00 item billed at 10.00: client totals trusted
    assert o["GET /order_history"].startswith("EXC")  # route has no template
    paid4 = o["invoice"][3]
    assert paid4[8] == "Paid" and paid4[9] == 218.7  # Paid for 218.70 against a 10.00 invoice


def test_django_reproduces_legacy_invoice_business_rules(manager, make_product, customer, supplier):
    """Replay the legacy scenario in Django and compare to what Flask+triggers produced."""
    apple = make_product("Apple", "70", "50", stock=200, sku="1")  # legacy stock is 200 after its trigger bug
    milk = make_product("Milk", "45", "30", stock=100, sku="2")
    apple.supplier = supplier
    apple.save()
    c1 = Customer.objects.create(name="Cust A", mobile="0123")
    inv1, _ = create_invoice(
        user=manager,
        customer=c1,
        invoice_date=__import__("datetime").date(2025, 1, 10),
        discount_percent=D("10"),
        lines=[
            {"product_id": apple.pk, "quantity": 3, "discount": D("10")},
            {"product_id": milk.pk, "quantity": 1, "discount": D("2")},
        ],
        payments=[{"method": "cash", "amount": D("218.70")}],
    )
    legacy1 = GOLDEN["invoice"][0]
    # legacy columns: no, cust_id, name, date, total_amount, discount, final_amount, status, payment_status, total_paid, method
    assert (inv1.subtotal, inv1.discount_amount, inv1.total) == (
        D(str(legacy1[4])),
        D(str(legacy1[5])),
        D(str(legacy1[6])),
    )
    assert (inv1.status, inv1.payment_status, inv1.total_paid) == (legacy1[7], legacy1[8], D(str(legacy1[9])))
    legacy_lines = [r for r in GOLDEN["invoice_products"] if r[0] == legacy1[0]]
    assert [(l.quantity, l.unit_price, l.discount, l.net_amount) for l in inv1.lines.order_by("id")] == [
        (r[2], D(str(r[4])), D(str(r[5])), D(str(r[6]))) for r in legacy_lines
    ]
    pay = Payment.objects.get(invoice=inv1)
    legacy_pay = [r for r in GOLDEN["payment"] if r[0] == legacy1[0]][0]
    assert (pay.amount, pay.method) == (D(str(legacy_pay[1])), legacy_pay[2])
    legacy_ledger = [r for r in GOLDEN["ledger"] if r[0] == "0123"]
    assert [
        (e.entry_type, e.amount, e.balance) for e in LedgerEntry.objects.filter(customer=c1).order_by("id")
    ] == [(r[2], D(str(r[1])), D(str(r[3]))) for r in legacy_ledger]
    # unpaid invoice -> Pending/Unpaid, ledger debit only
    c2 = Customer.objects.create(name="Cust B", mobile="0456")
    inv2, _ = create_invoice(user=manager, customer=c2, lines=[{"product_id": apple.pk, "quantity": 2}])
    legacy2 = GOLDEN["invoice"][1]
    assert (inv2.total, inv2.status, inv2.payment_status) == (D(str(legacy2[6])), legacy2[7], legacy2[8])
    # the big sale, then goods receipt of 25 + legacy add_item supplier-invoice (cost x qty, Pending/Unpaid)
    c3 = Customer.objects.create(name="Cust C", mobile="0789")
    create_invoice(user=manager, customer=c3, lines=[{"product_id": apple.pk, "quantity": 100}])
    c4 = Customer.objects.create(name="Cust D", mobile="0999")
    create_invoice(user=manager, customer=c4, lines=[{"product_id": apple.pk, "quantity": 1}])
    receipt, sinv = quick_receive(product=apple, quantity=25, user=manager)
    legacy_si = GOLDEN["supplier_invoice"][0]
    assert (sinv.total_amount, sinv.status, sinv.payment_status) == (
        D(str(legacy_si[4])),
        legacy_si[5],
        legacy_si[6],
    )
    apple.refresh_from_db()
    milk.refresh_from_db()
    legacy_stock = {r[0]: r[1] for r in GOLDEN["inventory"]}
    # legacy inventory ended at [119, 99] for [Apple, Milk]; the replay applies the same movements
    assert (apple.stock_on_hand, milk.stock_on_hand) == (legacy_stock[1], legacy_stock[2])
    assert reconcile() == []


def test_django_rejects_what_legacy_accepted_tampered_totals(manager, make_product, customer):
    p = make_product("Apple", "70", "50", stock=10)
    inv, _ = create_invoice(
        user=manager,
        customer=customer,
        lines=[{"product_id": p.pk, "quantity": 1}],
        payments=[{"method": "cash", "amount": D("70")}],
    )
    assert inv.total == D("70.00")  # legacy stored 10.00 from the forged form
    from apps.core.exceptions import BusinessRuleError

    with pytest.raises(BusinessRuleError):
        create_invoice(
            user=manager,
            customer=customer,
            lines=[{"product_id": p.pk, "quantity": 1}],
            payments=[{"method": "cash", "amount": D("218.70")}],
        )  # legacy accepted Paid 218.70 on a 10.00 invoice


def test_import_of_real_legacy_output_preserves_everything(legacy_run, legacy_cursor):
    _, observed = legacy_run
    rep = legacy_import.run(cursor=legacy_cursor, allow_quarantine=False)
    assert rep.totals["verification_mismatches"] == []
    assert Invoice.objects.count() == len(GOLDEN["invoice"]) == rep.counts_target["invoices"]
    # primary keys and money preserved exactly
    for row in GOLDEN["invoice"]:
        inv = Invoice.objects.get(pk=row[0])
        assert (
            inv.number == str(row[0])
            and inv.pk == row[0]
            and inv.total == D(str(row[6]))
            and inv.discount_amount == D(str(row[5]))
        )
        assert inv.customer_name == row[2] and inv.is_legacy
    assert {p.pk: p.stock_on_hand for p in Product.objects.all()} == {r[0]: r[1] for r in GOLDEN["inventory"]}
    assert Product.objects.get(pk=1).sku == "1" and Product.objects.get(pk=1).name == "Apple"
    assert reconcile() == []
    # legacy supplier invoice number kept, year-sequence advanced so the next one cannot collide
    assert SupplierInvoice.objects.count() == 1
    # the forged legacy invoice (#4) is flagged, not silently "fixed"
    kinds = {(a["kind"], a["ref"]) for a in rep.anomalies}
    forged = str(GOLDEN["invoice"][3][0])
    assert ("overpaid_invoice", forged) in kinds and ("lines_do_not_sum", forged) in kinds
    # ledger rows were carried over verbatim (source=legacy)
    assert LedgerEntry.objects.filter(source="legacy").count() == len(GOLDEN["ledger"])
    # new documents continue after the legacy numbers
    from apps.accounts.models import User
    from apps.sales.services import create_invoice as ci

    user = User.objects.create_user("importer", password="x")
    new, _ = ci(user=user, customer=Customer.objects.first(), lines=[{"product_id": 2, "quantity": 1}])
    assert int(new.number) > max(r[0] for r in GOLDEN["invoice"])
    pk_max = Invoice.objects.order_by("-id").first().pk
    assert pk_max == new.pk  # sequence reset: no primary-key collision
    assert not Payment.objects.filter(status="pending", invoice__payment_status="Paid").exists()


def test_import_refuses_non_empty_target_and_leaves_legacy_untouched(legacy_run, legacy_cursor, customer):
    with pytest.raises(legacy_import.LegacyImportError, match="already contains"):
        legacy_import.run(cursor=legacy_cursor)
    legacy_cursor.execute("SELECT count(*) FROM invoice")
    assert legacy_cursor.fetchone()[0] == len(GOLDEN["invoice"])


def test_import_dry_run_rolls_back(legacy_run, legacy_cursor):
    legacy_import.run(cursor=legacy_cursor, dry_run=True)
    assert (
        Invoice.objects.count() == 0 and Product.objects.count() == 0 and StockMovement.objects.count() == 0
    )


def test_legacy_connection_is_read_only(legacy_run, legacy_cursor):
    import psycopg

    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        legacy_cursor.execute("UPDATE customer SET name='hacked'")


def test_settings_force_read_only_legacy_alias():
    import os
    import subprocess
    import sys

    env = {**os.environ, "LEGACY_DATABASE_URL": "postgres://u:p@localhost:5432/old"}
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; import django, os; "
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); "
            "print(settings.DATABASES['legacy']['OPTIONS']['options'])",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "default_transaction_read_only=on" in out.stdout, out.stderr
