from decimal import Decimal as D

import pytest

from apps.core.exceptions import BusinessRuleError
from apps.customers.models import LedgerEntry
from apps.customers.services import ledger_balance, outstanding_for
from apps.inventory.models import StockMovement
from apps.inventory.services import reconcile
from apps.payments.models import Payment
from apps.payments.services import issue_refund, record_payment, reverse_payment
from apps.returns.models import SalesReturn
from apps.returns.services import approve_return, reject_return, request_return
from apps.sales.models import Invoice
from apps.sales.services import create_invoice


@pytest.fixture
def inv(manager, product, customer):
    invoice, _ = create_invoice(user=manager, customer=customer,
                                lines=[{"product_id": product.pk, "quantity": 4}])  # 280.00
    return invoice


def test_partial_then_full_payment(manager, inv):
    record_payment(inv.pk, amount=D("100"), method="cash", user=manager)
    inv.refresh_from_db()
    assert (inv.payment_status, inv.status, inv.total_paid) == ("Partially Paid", "Pending", D("100.00"))
    record_payment(inv.pk, amount=D("180"), method="bank_transfer", user=manager)
    inv.refresh_from_db()
    assert (inv.payment_status, inv.status, inv.balance_due) == ("Paid", "Completed", D("0.00"))
    assert ledger_balance(inv.customer_id) == D("0.00")


def test_overpayment_rejected(manager, inv):
    with pytest.raises(BusinessRuleError, match="exceeds the outstanding"):
        record_payment(inv.pk, amount=D("280.01"), method="cash", user=manager)
    assert Payment.objects.count() == 0


@pytest.mark.parametrize("amount", ["0", "-5"])
def test_non_positive_payment_rejected(manager, inv, amount):
    with pytest.raises(BusinessRuleError):
        record_payment(inv.pk, amount=D(amount), method="cash", user=manager)


def test_unknown_method_rejected(manager, inv):
    with pytest.raises(BusinessRuleError, match="Unknown payment method"):
        record_payment(inv.pk, amount=D("1"), method="bitcoin", user=manager)


def test_idempotency_key_prevents_duplicate_payment(manager, inv):
    p1, c1 = record_payment(inv.pk, amount=D("50"), method="cash", user=manager, idempotency_key="k1")
    p2, c2 = record_payment(inv.pk, amount=D("50"), method="cash", user=manager, idempotency_key="k1")
    assert c1 and not c2 and p1.pk == p2.pk and Payment.objects.count() == 1


def test_reverse_payment_reopens_balance(manager, inv):
    p, _ = record_payment(inv.pk, amount=D("280"), method="cash", user=manager)
    reverse_payment(p.pk, user=manager, reason="bounced")
    inv.refresh_from_db()
    assert (inv.payment_status, inv.total_paid) == ("Unpaid", D("0.00"))
    assert ledger_balance(inv.customer_id) == D("280.00")
    with pytest.raises(BusinessRuleError):
        reverse_payment(p.pk, user=manager, reason="again")
    with pytest.raises(BusinessRuleError, match="reason"):
        reverse_payment(p.pk, user=manager, reason="")


def test_cancelled_invoice_takes_no_payment(manager, inv):
    from apps.sales.services import cancel_invoice
    cancel_invoice(inv.pk, user=manager, reason="oops")
    with pytest.raises(BusinessRuleError, match="cancelled"):
        record_payment(inv.pk, amount=D("10"), method="cash", user=manager)


def test_refund_cannot_exceed_received(manager, inv):
    record_payment(inv.pk, amount=D("100"), method="cash", user=manager)
    with pytest.raises(BusinessRuleError, match="exceeds the amount received"):
        issue_refund(inv.pk, amount=D("100.01"), method="cash", user=manager)


# ---- returns -------------------------------------------------------------
def line_of(inv):
    return inv.lines.get()


def test_return_resellable_paid_invoice_refunds_and_restocks(manager, product, inv):
    record_payment(inv.pk, amount=D("280"), method="cash", user=manager)
    ret = request_return(invoice_id=inv.pk, reason="wrong item", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 1, "disposition": "resellable"}])
    assert ret.status == "pending"
    product.refresh_from_db(); assert product.stock_on_hand == 96  # nothing moves until approved
    approve_return(ret.pk, user=manager, refund_method="cash")
    product.refresh_from_db(); inv.refresh_from_db(); ret.refresh_from_db()
    assert product.stock_on_hand == 97
    assert ret.credit_amount == D("70.00") and ret.refund_amount == D("70.00")
    assert (inv.returned_total, inv.total_paid, inv.balance_due) == (D("70.00"), D("210.00"), D("0.00"))
    assert inv.payment_status == "Paid"
    assert ledger_balance(inv.customer_id) == D("0.00")
    assert reconcile() == []


def test_return_damaged_is_written_off_not_restocked(manager, product, inv):
    record_payment(inv.pk, amount=D("280"), method="cash", user=manager)
    ret = request_return(invoice_id=inv.pk, reason="broken", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 2, "disposition": "damaged"}])
    approve_return(ret.pk, user=manager, refund_method="cash")
    product.refresh_from_db()
    assert product.stock_on_hand == 96  # +2 back, -2 written off
    kinds = list(StockMovement.objects.filter(reference_type="sales_return").values_list("movement_type", flat=True))
    assert sorted(kinds) == ["return_damaged", "sale_return"]
    assert reconcile() == []


def test_return_on_unpaid_invoice_reduces_balance_without_refund(manager, inv):
    ret = request_return(invoice_id=inv.pk, reason="x", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 1, "disposition": "resellable"}])
    approve_return(ret.pk, user=manager)  # no refund method needed: nothing was paid
    inv.refresh_from_db(); ret.refresh_from_db()
    assert ret.refund_amount == D("0.00") and inv.balance_due == D("210.00")
    assert outstanding_for(inv.customer_id) == D("210.00")


def test_cannot_return_more_than_sold_cumulatively(manager, inv):
    l = line_of(inv)
    request_return(invoice_id=inv.pk, reason="a", user=manager,
                   lines=[{"invoice_line_id": l.pk, "quantity": 3, "disposition": "resellable"}])
    with pytest.raises(BusinessRuleError, match="only 1 unit"):
        request_return(invoice_id=inv.pk, reason="b", user=manager,
                       lines=[{"invoice_line_id": l.pk, "quantity": 2, "disposition": "resellable"}])


def test_full_return_marks_invoice_returned(manager, inv):
    ret = request_return(invoice_id=inv.pk, reason="all back", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 4, "disposition": "resellable"}])
    approve_return(ret.pk, user=manager)
    inv.refresh_from_db()
    assert inv.status == "Returned" and inv.returned_total == D("280.00")
    from apps.sales.services import cancel_invoice
    with pytest.raises(BusinessRuleError, match="has returns"):
        cancel_invoice(inv.pk, user=manager, reason="x")


def test_refund_method_required_when_money_is_owed(manager, inv):
    record_payment(inv.pk, amount=D("280"), method="cash", user=manager)
    ret = request_return(invoice_id=inv.pk, reason="x", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 1, "disposition": "resellable"}])
    with pytest.raises(BusinessRuleError, match="how the refund"):
        approve_return(ret.pk, user=manager)
    ret.refresh_from_db(); inv.refresh_from_db()
    assert ret.status == "pending" and inv.returned_total == 0  # atomic: nothing half-applied


def test_double_approval_blocked_and_reject(manager, inv):
    ret = request_return(invoice_id=inv.pk, reason="x", user=manager, lines=[
        {"invoice_line_id": line_of(inv).pk, "quantity": 1, "disposition": "resellable"}])
    approve_return(ret.pk, user=manager)
    with pytest.raises(BusinessRuleError, match="already processed"):
        approve_return(ret.pk, user=manager)
    with pytest.raises(BusinessRuleError, match="already processed"):
        reject_return(ret.pk, user=manager, note="late")


def test_return_with_discount_and_tax_credits_paid_value(manager, make_product, customer):
    p = make_product("Taxed", "100", "60", stock=10, tax="10")
    inv, _ = create_invoice(user=manager, customer=customer, discount_percent=D("10"),
                            lines=[{"product_id": p.pk, "quantity": 2}])
    # subtotal 200, discount 20, taxable 180, tax 18 => total 198
    assert inv.total == D("198.00")
    record_payment(inv.pk, amount=D("198"), method="cash", user=manager)
    ret = request_return(invoice_id=inv.pk, reason="x", user=manager, lines=[
        {"invoice_line_id": inv.lines.get().pk, "quantity": 1, "disposition": "resellable"}])
    approve_return(ret.pk, user=manager, refund_method="cash")
    ret.refresh_from_db()
    assert ret.credit_amount == D("99.00")  # half of (180 + 18)
