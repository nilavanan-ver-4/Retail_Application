import threading
from decimal import Decimal as D

import pytest
from django.db import connection

from apps.audit.models import AuditLog
from apps.core.exceptions import BusinessRuleError, InsufficientStock
from apps.customers.models import Customer, LedgerEntry
from apps.inventory.models import StockMovement
from apps.inventory.services import reconcile
from apps.payments.models import Payment
from apps.products.models import Product
from apps.sales.models import Invoice
from apps.sales.services import cancel_invoice, create_invoice


def make_invoice(user, product, customer=None, qty=1, payments=(), **kw):
    return create_invoice(
        user=user,
        customer=customer,
        customer_mobile="0111",
        customer_name="Walk In",
        lines=[{"product_id": product.pk, "quantity": qty}],
        payments=payments,
        **kw,
    )


def test_characterization_legacy_scenario(manager, make_product, customer):
    """Same scenario the ORIGINAL Flask app was run through (tests/characterization/legacy_golden.json)."""
    apple = make_product("Apple", "70", "50", stock=200)
    milk = make_product("Milk", "45", "30", stock=100)
    inv, created = create_invoice(
        user=manager,
        customer=customer,
        discount_percent=D("10"),
        lines=[
            {"product_id": apple.pk, "quantity": 3, "discount": D("10")},
            {"product_id": milk.pk, "quantity": 1, "discount": D("2")},
        ],
        payments=[{"method": "cash", "amount": D("218.70")}],
    )
    assert created
    assert (inv.subtotal, inv.discount_amount, inv.total) == (D("243.00"), D("24.30"), D("218.70"))
    assert (inv.status, inv.payment_status, inv.total_paid) == ("Completed", "Paid", D("218.70"))
    assert [l.net_amount for l in inv.lines.all()] == [
        D("200.00"),
        D("43.00"),
    ]  # legacy invoice_products.total
    apple.refresh_from_db()
    milk.refresh_from_db()
    assert (apple.stock_on_hand, milk.stock_on_hand) == (197, 99)
    pay = Payment.objects.get(invoice=inv)
    assert (pay.amount, pay.method, pay.status) == (D("218.70"), "cash", "completed")
    led = list(LedgerEntry.objects.filter(customer=customer).order_by("id"))
    assert [(e.entry_type, e.amount, e.balance) for e in led] == [
        ("Debit", D("218.70"), D("218.70")),
        ("Credit", D("218.70"), D("0.00")),
    ]
    assert reconcile() == []


def test_unpaid_invoice_is_pending(manager, product, customer):
    inv, _ = make_invoice(manager, product, customer, qty=2)
    assert (inv.status, inv.payment_status, inv.total_paid) == ("Pending", "Unpaid", D("0.00"))
    assert inv.balance_due == D("140.00")


def test_partial_payment_is_partially_paid_not_paid(manager, product, customer):
    """Legacy bug fixed: legacy marked ANY paid method with total_paid>0 as 'Paid'."""
    inv, _ = make_invoice(manager, product, customer, qty=2, payments=[{"method": "cash", "amount": D("40")}])
    assert (inv.status, inv.payment_status) == ("Pending", "Partially Paid")
    assert inv.balance_due == D("100.00")


def test_split_payment(manager, product, customer):
    inv, _ = make_invoice(
        manager,
        product,
        customer,
        qty=2,
        payments=[
            {"method": "cash", "amount": D("100")},
            {"method": "upi", "amount": D("40"), "reference": "T123"},
        ],
    )
    assert inv.payment_status == "Paid" and inv.payments.count() == 2


def test_browser_cannot_set_price(manager, cashier, product, customer):
    with pytest.raises(BusinessRuleError, match="not allowed to change the catalogue price"):
        create_invoice(
            user=cashier,
            customer=customer,
            lines=[{"product_id": product.pk, "quantity": 1, "unit_price": D("1.00")}],
        )
    inv, _ = create_invoice(
        user=cashier,
        customer=customer,
        lines=[{"product_id": product.pk, "quantity": 1, "unit_price": D("70.00")}],
    )
    assert inv.total == D("70.00")  # same price is fine


def test_price_override_allowed_and_audited(manager, product, customer):
    inv, _ = create_invoice(
        user=manager,
        customer=customer,
        can_override_price=True,
        lines=[{"product_id": product.pk, "quantity": 1, "unit_price": D("60.00")}],
    )
    assert inv.total == D("60.00")
    assert AuditLog.objects.filter(action="invoice.price_override").exists()


def test_insufficient_stock_rejected_and_nothing_written(manager, make_product, customer):
    p = make_product(stock=5)
    before = Invoice.objects.count()
    with pytest.raises(InsufficientStock):
        make_invoice(manager, p, customer, qty=6)
    assert Invoice.objects.count() == before
    p.refresh_from_db()
    assert p.stock_on_hand == 5 and not LedgerEntry.objects.exists()


def test_same_product_on_two_lines_is_checked_in_aggregate(manager, make_product, customer):
    p = make_product(stock=5)
    with pytest.raises(InsufficientStock):
        create_invoice(
            user=manager,
            customer=customer,
            lines=[{"product_id": p.pk, "quantity": 3}, {"product_id": p.pk, "quantity": 3}],
        )


@pytest.mark.parametrize(
    "kw,msg",
    [
        (dict(lines=[]), "at least one product"),
        (dict(discount_percent=D("150")), "between 0 and 100"),
        (dict(payments=[{"method": "cash", "amount": D("0")}]), "greater than zero"),
        (dict(payments=[{"method": "cash", "amount": D("500")}]), "exceed the invoice total"),
    ],
)
def test_invalid_invoice_inputs(manager, product, customer, kw, msg):
    args = dict(user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 1}])
    args.update(kw)
    with pytest.raises(BusinessRuleError, match=msg):
        create_invoice(**args)


def test_zero_value_invoice_rejected(manager, make_product, customer):
    free = make_product("Free", "0", "0")
    with pytest.raises(BusinessRuleError, match="greater than zero"):
        make_invoice(manager, free, customer)


def test_negative_and_fractional_quantity_rejected(manager, product, customer):
    for q in (-1, 0, D("1.5")):
        with pytest.raises(BusinessRuleError):
            create_invoice(user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": q}])


def test_archived_product_cannot_be_sold(manager, product, customer):
    product.archive()
    with pytest.raises(BusinessRuleError, match="archived"):
        make_invoice(manager, product, customer)


def test_credit_limit_enforced_unless_override(manager, product, customer):
    customer.credit_limit = D("100.00")
    customer.save()
    with pytest.raises(BusinessRuleError, match="credit limit"):
        make_invoice(manager, product, customer, qty=2)  # 140 > 100
    inv, _ = make_invoice(manager, product, customer, qty=2, can_override_credit=True)
    assert inv.pk


def test_new_customer_auto_created_like_legacy(manager, product):
    inv, _ = create_invoice(
        user=manager,
        customer_mobile="0777",
        customer_name="New Guy",
        lines=[{"product_id": product.pk, "quantity": 1}],
    )
    assert Customer.objects.get(mobile="0777").name == "New Guy"
    assert inv.customer_name == "New Guy"


def test_duplicate_submission_token_returns_same_invoice(manager, product, customer):
    a, created_a = make_invoice(manager, product, customer, submission_token="tok-1")
    b, created_b = make_invoice(manager, product, customer, submission_token="tok-1")
    assert created_a and not created_b and a.pk == b.pk
    product.refresh_from_db()
    assert product.stock_on_hand == 99 and Invoice.objects.count() == 1


def test_invoice_numbers_sequential_from_legacy_start(manager, product, customer):
    a, _ = make_invoice(manager, product, customer)
    b, _ = make_invoice(manager, product, customer)
    assert (a.number, b.number) == ("100000", "100001")


def test_cancel_restores_stock_and_ledger(manager, product, customer):
    inv, _ = make_invoice(manager, product, customer, qty=4)
    product.refresh_from_db()
    assert product.stock_on_hand == 96
    cancel_invoice(inv.pk, user=manager, reason="customer changed mind")
    product.refresh_from_db()
    inv.refresh_from_db()
    assert product.stock_on_hand == 100 and inv.status == "Cancelled"
    bal = LedgerEntry.objects.filter(customer=customer).order_by("-id").first().balance
    assert bal == D("0.00")
    assert AuditLog.objects.filter(action="invoice.cancelled", target_id=str(inv.pk)).exists()
    with pytest.raises(BusinessRuleError, match="already cancelled"):
        cancel_invoice(inv.pk, user=manager, reason="again")


def test_cancel_requires_reason_and_no_money_held(manager, product, customer):
    inv, _ = make_invoice(manager, product, customer, payments=[{"method": "cash", "amount": D("70")}])
    with pytest.raises(BusinessRuleError, match="reason"):
        cancel_invoice(inv.pk, user=manager, reason=" ")
    with pytest.raises(BusinessRuleError, match="Reverse or refund"):
        cancel_invoice(inv.pk, user=manager, reason="x")


def test_db_constraint_blocks_negative_stock_even_bypassing_services(product):
    from django.db import IntegrityError, transaction

    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.filter(pk=product.pk).update(stock_on_hand=-1)


def test_db_constraint_enforces_invoice_total_equation(manager, product, customer):
    from django.db import IntegrityError, transaction

    inv, _ = make_invoice(manager, product, customer)
    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.filter(pk=inv.pk).update(total=D("1.00"))


@pytest.mark.django_db(transaction=True)
def test_concurrent_sales_never_oversell(manager, make_product, customer):
    """10 threads race for 5 units: exactly 5 succeed, stock ends at 0, never negative."""
    p = make_product("Hot item", stock=5)
    results, lock = [], threading.Lock()

    def buy(i):
        try:
            create_invoice(
                user=manager,
                customer=customer,
                submission_token=f"race-{i}",
                lines=[{"product_id": p.pk, "quantity": 1}],
            )
            outcome = "ok"
        except InsufficientStock:
            outcome = "oos"
        except Exception as exc:  # noqa: BLE001
            outcome = f"err:{exc!r}"
        finally:
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=buy, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok"] * 5 + ["oos"] * 5, results
    p.refresh_from_db()
    assert p.stock_on_hand == 0
    assert Invoice.objects.count() == 5
    assert reconcile() == []
    nums = list(Invoice.objects.values_list("number", flat=True))
    assert len(nums) == len(set(nums))  # unique, no duplicate numbers


@pytest.mark.django_db(transaction=True)
def test_concurrent_duplicate_submission_creates_one_invoice(manager, make_product, customer):
    p = make_product("Dup", stock=10)
    out = []

    def go():
        try:
            inv, created = create_invoice(
                user=manager,
                customer=customer,
                submission_token="same-token",
                lines=[{"product_id": p.pk, "quantity": 1}],
            )
            out.append((inv.pk, created))
        finally:
            connection.close()

    ts = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert Invoice.objects.count() == 1
    assert len({pk for pk, _ in out}) == 1 and sum(c for _, c in out) == 1
    p.refresh_from_db()
    assert p.stock_on_hand == 9


def test_stock_movement_ledger_is_immutable(product):
    m = StockMovement.objects.first()
    with pytest.raises(PermissionError):
        m.quantity = 5
        m.save()
    with pytest.raises(PermissionError):
        m.delete()
