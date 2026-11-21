"""Regression tests for the retail architecture audit; all records are synthetic."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from threading import Barrier

import pytest
from django.contrib import admin
from django.db import connection
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.core.exceptions import BusinessRuleError
from apps.core.money import q2, to_decimal
from apps.customers.models import LedgerEntry
from apps.customers.services import ledger_balance
from apps.inventory.services import reconcile, save_stocktake_counts, start_stocktake
from apps.payments.models import Payment
from apps.payments.services import issue_refund, record_payment, reverse_payment
from apps.purchasing import services as purchasing
from apps.purchasing.models import GoodsReceipt, PurchaseOrder, SupplierInvoice, SupplierPayment
from apps.returns.models import SalesReturn
from apps.returns.services import approve_return, request_return
from apps.sales.models import Invoice
from apps.sales.services import cancel_invoice, create_invoice
from apps.suppliers.models import Supplier


def sale(manager, customer, product, **kwargs):
    return create_invoice(
        user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 2}], **kwargs
    )[0]


def supplier_bill(manager, supplier, product):
    return purchasing.record_supplier_invoice(
        supplier=supplier,
        invoice_number=None,
        invoice_date=timezone.localdate(),
        due_date=None,
        user=manager,
        lines=[{"product": product, "quantity": 10, "unit_cost": D("50")}],
    )


def return_items(invoice, manager, quantity):
    return request_return(
        invoice_id=invoice.pk,
        reason="Returned goods",
        user=manager,
        lines=[
            {"invoice_line_id": invoice.lines.get().pk, "quantity": quantity, "disposition": "resellable"}
        ],
    )


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity", "invalid", D("NaN")])
def test_non_finite_money_is_a_business_error(bad):
    assert to_decimal(bad) is None
    with pytest.raises(BusinessRuleError):
        q2(bad)


def test_non_finite_bulk_price_is_reported_without_changes(client_as, manager, product):
    client = client_as(manager)
    for value in ("Infinity", "NaN"):
        response = client.post(
            reverse("products:bulk"),
            {
                "selected": [product.pk],
                "action": "price_percent",
                "value": value,
            },
            follow=True,
        )
        assert response.status_code == 200 and b"Bulk update failed" in response.content
        product.refresh_from_db()
        assert product.selling_price == 70


def test_exact_barcode_is_not_displaced_by_name_matches(client_as, cashier, make_product):
    for index in range(13):
        make_product(name=f"A barcode123 match {index}")
    scanned = make_product(name="Z actual scanned product", barcode="barcode123")
    results = client_as(cashier).get(reverse("sales:product_search"), {"q": "barcode123"}).json()["products"]
    assert results[0]["id"] == scanned.pk and len(results) == 12


def test_split_payment_rounding_cannot_bypass_credit_limit(manager, customer, product):
    customer.credit_limit = D("139.97")
    customer.save()
    with pytest.raises(BusinessRuleError, match="credit limit"):
        sale(
            manager,
            customer,
            product,
            payments=[
                {"amount": D("0.014"), "method": "cash"},
                {"amount": D("0.014"), "method": "cash"},
            ],
        )
    assert not Invoice.objects.exists()


def test_payment_token_is_bound_to_operation(manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="10", method="cash", user=manager, idempotency_key="bound")
    with pytest.raises(BusinessRuleError, match="different operation"):
        record_payment(inv.pk, amount="20", method="cash", user=manager, idempotency_key="bound")
    with pytest.raises(BusinessRuleError, match="different operation"):
        issue_refund(inv.pk, amount="10", method="cash", user=manager, idempotency_key="bound")
    other = sale(manager, customer, product)
    with pytest.raises(BusinessRuleError, match="different operation"):
        record_payment(other.pk, amount="10", method="cash", user=manager, idempotency_key="bound")
    assert Payment.objects.count() == 1


def test_reversals_cannot_make_net_received_negative(manager, customer, product):
    inv = sale(manager, customer, product)
    payment, _ = record_payment(inv.pk, amount="140", method="cash", user=manager)
    issue_refund(inv.pk, amount="20", method="cash", user=manager)
    with pytest.raises(BusinessRuleError, match="negative"):
        reverse_payment(payment.pk, user=manager, reason="Error")
    inv.refresh_from_db()
    payment.refresh_from_db()
    assert inv.total_paid == 120 and payment.status == "completed"
    assert ledger_balance(customer.pk) == 20


def test_return_refund_cannot_be_reversed_into_overpayment(manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="140", method="cash", user=manager)
    ret = return_items(inv, manager, 1)
    approve_return(ret.pk, user=manager, refund_method="cash")
    refund = Payment.objects.get(kind="refund")
    with pytest.raises(BusinessRuleError, match="overpay"):
        reverse_payment(refund.pk, user=manager, reason="Error")
    assert ledger_balance(customer.pk) == 0


def test_cancelled_invoice_refund_cannot_be_reversed(manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="140", method="cash", user=manager)
    refund = issue_refund(inv.pk, amount="140", method="cash", user=manager)
    cancel_invoice(inv.pk, user=manager, reason="Cancelled sale")
    with pytest.raises(BusinessRuleError, match="cancelled"):
        reverse_payment(refund.pk, user=manager, reason="Error")
    assert ledger_balance(customer.pk) == 0


def test_unknown_refund_method_is_rejected(manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="10", method="cash", user=manager)
    with pytest.raises(BusinessRuleError, match="Unknown payment method"):
        issue_refund(inv.pk, amount="5", method="unknown", user=manager)
    assert not Payment.objects.filter(kind="refund").exists()


def test_full_return_leaves_shipping_collectable_through_http(client_as, manager, customer, product):
    inv = sale(manager, customer, product, shipping_charge=D("5"))
    ret = return_items(inv, manager, 2)
    approve_return(ret.pk, user=manager)
    inv.refresh_from_db()
    assert (inv.status, inv.payment_status, inv.balance_due) == ("Returned", "Unpaid", D("5"))
    client = client_as(manager)
    assert b"Record payment" in client.get(inv.get_absolute_url()).content
    response = client.post(
        reverse("payments:create", args=[inv.pk]),
        {
            "amount": "5",
            "method": "cash",
            "paid_on": timezone.localdate(),
            "token": "shipping",
        },
    )
    assert response.status_code == 302
    inv.refresh_from_db()
    assert (inv.status, inv.payment_status, inv.balance_due) == ("Returned", "Paid", D("0"))
    assert ledger_balance(customer.pk) == 0


def test_duplicate_or_fractional_return_lines_are_rejected(manager, customer, product):
    inv = sale(manager, customer, product)
    line = {"invoice_line_id": inv.lines.get().pk, "quantity": 1, "disposition": "resellable"}
    with pytest.raises(BusinessRuleError, match="only once"):
        request_return(invoice_id=inv.pk, reason="x", user=manager, lines=[line, line])
    with pytest.raises(BusinessRuleError, match="whole numbers"):
        return_items(inv, manager, D("1.5"))
    assert inv.returns.count() == 0


def test_zero_value_return_blocks_cancellation(manager, customer, make_product):
    free = make_product(price="0", stock=10)
    inv = sale(manager, customer, free, shipping_charge=D("5"))
    approve_return(return_items(inv, manager, 1).pk, user=manager)
    with pytest.raises(BusinessRuleError, match="has returns"):
        cancel_invoice(inv.pk, user=manager, reason="x")
    assert reconcile() == []
    free.refresh_from_db()
    assert free.stock_on_hand == 9


def test_all_value_returned_does_not_hide_remaining_free_goods(client_as, manager, customer, make_product):
    paid = make_product(price="10", stock=5)
    free = make_product(price="0", stock=5)
    inv, _ = create_invoice(
        user=manager,
        customer=customer,
        lines=[
            {"product_id": paid.pk, "quantity": 1},
            {"product_id": free.pk, "quantity": 1},
        ],
    )
    record_payment(inv.pk, amount="10", method="cash", user=manager)
    paid_line = inv.lines.get(product=paid)
    ret = request_return(
        invoice_id=inv.pk,
        reason="paid item",
        user=manager,
        lines=[
            {"invoice_line_id": paid_line.pk, "quantity": 1, "disposition": "resellable"},
        ],
    )
    approve_return(ret.pk, user=manager, refund_method="cash")
    inv.refresh_from_db()
    assert inv.status == "Completed" and inv.balance_due == 0
    assert b"Return items" in client_as(manager).get(inv.get_absolute_url()).content
    free_line = inv.lines.get(product=free)
    ret = request_return(
        invoice_id=inv.pk,
        reason="free item",
        user=manager,
        lines=[
            {"invoice_line_id": free_line.pk, "quantity": 1, "disposition": "resellable"},
        ],
    )
    approve_return(ret.pk, user=manager)
    inv.refresh_from_db()
    assert inv.status == "Returned"
    assert reconcile() == [] and ledger_balance(customer.pk) == 0


def test_fully_free_goods_return_preserves_shipping_balance(manager, customer, make_product):
    free = make_product(price="0", stock=10)
    inv = sale(manager, customer, free, shipping_charge=D("5"))
    approve_return(return_items(inv, manager, 2).pk, user=manager)
    inv.refresh_from_db()
    assert (inv.status, inv.payment_status, inv.balance_due) == ("Returned", "Unpaid", D("5"))


def test_legacy_duplicate_return_lines_cannot_be_approved(manager, customer, product):
    inv = sale(manager, customer, product)
    ret = return_items(inv, manager, 1)
    line = ret.lines.get()
    ret.lines.create(invoice_line=line.invoice_line, quantity=1, disposition="resellable")
    with pytest.raises(BusinessRuleError, match="repeats an invoice line"):
        approve_return(ret.pk, user=manager)
    inv.refresh_from_db()
    product.refresh_from_db()
    assert inv.returned_total == 0 and product.stock_on_hand == 98


def test_cashier_cannot_change_credit_limits(client_as, cashier, manager, customer):
    customer.credit_limit = D("100")
    customer.save()
    payload = {"name": customer.name, "mobile": customer.mobile, "is_active": "on", "credit_limit": "999999"}
    client = client_as(cashier)
    assert b'name="credit_limit"' not in client.get(reverse("customers:update", args=[customer.pk])).content
    client.post(reverse("customers:update", args=[customer.pk]), payload)
    customer.refresh_from_db()
    assert customer.credit_limit == 100
    client_as(manager).post(reverse("customers:update", args=[customer.pk]), payload)
    customer.refresh_from_db()
    assert customer.credit_limit == 999999


def test_admin_cannot_bypass_transaction_services(client_as, admin_user, customer, product):
    admin_user.is_staff = admin_user.is_superuser = True
    admin_user.save()
    request = RequestFactory().get("/admin/")
    request.user = admin_user
    for model in (
        Invoice,
        PurchaseOrder,
        GoodsReceipt,
        SupplierInvoice,
        SupplierPayment,
        SalesReturn,
        LedgerEntry,
    ):
        registered = admin.site._registry[model]
        assert registered.has_view_permission(request)
        assert not registered.has_add_permission(request)
        assert not registered.has_change_permission(request)
        assert not registered.has_delete_permission(request)
    inv = sale(admin_user, customer, product)
    client = client_as(admin_user)
    assert client.get(reverse("admin:sales_invoice_change", args=[inv.pk])).status_code == 200
    assert (
        client.post(reverse("admin:sales_invoice_change", args=[inv.pk]), {"total": "1"}).status_code == 403
    )
    assert (
        client.post(reverse("admin:sales_invoice_delete", args=[inv.pk]), {"post": "yes"}).status_code == 403
    )
    inv.refresh_from_db()
    assert inv.total == 140 and inv.status == "Pending"


def test_inventory_staff_cannot_create_supplier_bill_via_shortcut(client_as, inventory_staff, product):
    response = client_as(inventory_staff).post(
        reverse("purchasing:quick_receive", args=[product.pk]), {"quantity": "2", "unit_cost": "50"}
    )
    assert response.status_code == 403
    assert SupplierInvoice.objects.count() == GoodsReceipt.objects.count() == 0


def test_supplier_page_hides_bills_without_permission(client_as, inventory_staff, manager, supplier, product):
    bill = supplier_bill(manager, supplier, product)
    html = client_as(inventory_staff).get(supplier.get_absolute_url()).content.decode()
    assert bill.invoice_number not in html and "Payable to supplier" not in html


def test_refund_export_respects_refund_permission(client_as, cashier, manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="10", method="cash", user=manager)
    refund = issue_refund(inv.pk, amount="5", method="cash", user=manager)
    client = client_as(cashier)
    assert refund.receipt_no not in client.get(reverse("payments:export")).content.decode()
    assert (
        refund.receipt_no not in client.get(reverse("payments:export"), {"kind": "refund"}).content.decode()
    )


def test_receipts_require_matching_supplier_order_and_product(manager, supplier, product, make_product):
    po = purchasing.create_purchase_order(
        supplier=supplier,
        order_date=timezone.localdate(),
        expected_date=None,
        user=manager,
        lines=[{"product": product, "quantity": 5, "unit_cost": D("50")}],
    )
    other = Supplier.objects.create(name="Other supplier")
    line = {"product": product, "quantity": 1, "unit_cost": D("50"), "po_line": po.lines.get()}
    args = {"supplier": supplier, "receipt_date": timezone.localdate(), "user": manager, "purchase_order": po}
    with pytest.raises(BusinessRuleError, match="different supplier"):
        purchasing.create_receipt(**{**args, "supplier": other}, lines=[line])
    with pytest.raises(BusinessRuleError, match="must match"):
        purchasing.create_receipt(**args, lines=[{**line, "product": make_product()}])
    with pytest.raises(BusinessRuleError, match="must match"):
        purchasing.create_receipt(**args, lines=[{**line, "po_line": None}])
    receipt = purchasing.create_receipt(**args, lines=[line])
    # Confirming also validates persisted linkage, including legacy/admin-created drafts.
    receipt.supplier = other
    receipt.save()
    with pytest.raises(BusinessRuleError, match="different supplier"):
        purchasing.confirm_receipt(receipt.pk, manager)
    product.refresh_from_db()
    assert product.stock_on_hand == 100


@pytest.mark.parametrize("quantity,cost", [(0, "50"), (D("1.5"), "50"), (1, "-1"), (1, "NaN")])
def test_purchase_inputs_validate_before_writes(manager, supplier, product, quantity, cost):
    with pytest.raises(BusinessRuleError):
        purchasing.create_receipt(
            supplier=supplier,
            receipt_date=timezone.localdate(),
            user=manager,
            lines=[{"product": product, "quantity": quantity, "unit_cost": D(cost)}],
        )
    assert not GoodsReceipt.objects.exists()


def test_order_discount_and_duplicate_products_are_rejected(manager, supplier, product):
    args = {"supplier": supplier, "order_date": timezone.localdate(), "expected_date": None, "user": manager}
    line = {"product": product, "quantity": 1, "unit_cost": D("50")}
    with pytest.raises(BusinessRuleError, match="discount"):
        purchasing.create_purchase_order(**args, lines=[line], discount=D("51"))
    with pytest.raises(BusinessRuleError, match="one line per product"):
        purchasing.create_purchase_order(**args, lines=[line, line])


def test_supplier_invoice_requires_confirmed_matching_receipt(manager, supplier, product):
    receipt = purchasing.create_receipt(
        supplier=supplier,
        receipt_date=timezone.localdate(),
        user=manager,
        lines=[{"product": product, "quantity": 1, "unit_cost": D("50")}],
    )
    args = {
        "supplier": supplier,
        "invoice_number": None,
        "invoice_date": timezone.localdate(),
        "due_date": None,
        "receipt": receipt,
        "user": manager,
        "lines": [{"product": product, "quantity": 1, "unit_cost": D("50")}],
    }
    with pytest.raises(BusinessRuleError, match="Confirm"):
        purchasing.record_supplier_invoice(**args)
    receipt = purchasing.confirm_receipt(receipt.pk, manager)
    other = Supplier.objects.create(name="Other supplier")
    with pytest.raises(BusinessRuleError, match="different supplier"):
        purchasing.record_supplier_invoice(**{**args, "supplier": other, "receipt": receipt})
    assert not SupplierInvoice.objects.exists()


def test_supplier_payment_token_and_method_validation(manager, supplier, product):
    inv = supplier_bill(manager, supplier, product)
    args = {
        "amount": "10",
        "method": "cash",
        "paid_on": timezone.localdate(),
        "user": manager,
        "idempotency_key": "supplier-bound",
    }
    first = purchasing.pay_supplier_invoice(inv.pk, **args)
    assert purchasing.pay_supplier_invoice(inv.pk, **args).pk == first.pk
    with pytest.raises(BusinessRuleError, match="different operation"):
        purchasing.pay_supplier_invoice(inv.pk, **{**args, "amount": "20"})
    with pytest.raises(BusinessRuleError, match="Unknown payment method"):
        purchasing.pay_supplier_invoice(inv.pk, **{**args, "method": "unknown", "idempotency_key": None})
    assert SupplierPayment.objects.count() == 1


def test_stocktake_count_batch_is_atomic_and_applied_counts_are_immutable(manager, product, make_product):
    other = make_product()
    st = start_stocktake("Audit count", manager)
    lines = {ln.product_id: ln for ln in st.lines.all()}
    with pytest.raises(BusinessRuleError, match="Invalid count"):
        save_stocktake_counts(st.pk, {lines[product.pk].pk: "90", lines[other.pk].pk: "bad"}, user=manager)
    assert not st.lines.filter(counted_quantity__isnull=False).exists()
    assert save_stocktake_counts(st.pk, {lines[product.pk].pk: "90"}, user=manager, apply=True) == 1
    with pytest.raises(BusinessRuleError, match="already applied"):
        save_stocktake_counts(st.pk, {lines[product.pk].pk: "80"}, user=manager)
    assert st.lines.get(product=product).counted_quantity == 90
    assert reconcile() == []


def test_stocktake_rejects_intervening_sale_without_overwriting_stock(
    manager, customer, product, make_product
):
    other = make_product()
    st = start_stocktake("Count before sale", manager)
    create_invoice(user=manager, customer=customer, lines=[{"product_id": other.pk, "quantity": 1}])
    counts = {line.pk: "90" for line in st.lines.all()}
    with pytest.raises(BusinessRuleError, match="stock moved"):
        save_stocktake_counts(st.pk, counts, user=manager, apply=True)
    product.refresh_from_db()
    other.refresh_from_db()
    st.refresh_from_db()
    assert (product.stock_on_hand, other.stock_on_hand, st.status) == (100, 99, "draft")
    assert not st.lines.filter(counted_quantity__isnull=False).exists()
    assert reconcile() == []


def concurrent_calls(operation, workers=6):
    barrier = Barrier(workers)

    def run():
        try:
            barrier.wait(timeout=15)
            return operation()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(run) for _ in range(workers)]
        return [f.result(timeout=30) for f in futures]


@pytest.mark.django_db(transaction=True)
def test_concurrent_payment_retry_records_exactly_once(manager, customer, product):
    inv = sale(manager, customer, product)
    results = concurrent_calls(
        lambda: record_payment(
            inv.pk, amount="140", method="cash", user=manager, idempotency_key="payment-race"
        )
    )
    assert len({p.pk for p, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    inv.refresh_from_db()
    assert inv.total_paid == 140 and ledger_balance(customer.pk) == 0


@pytest.mark.django_db(transaction=True)
def test_concurrent_refund_retry_records_exactly_once(manager, customer, product):
    inv = sale(manager, customer, product)
    record_payment(inv.pk, amount="140", method="cash", user=manager)
    results = concurrent_calls(
        lambda: issue_refund(inv.pk, amount="140", method="cash", user=manager, idempotency_key="refund-race")
    )
    assert len({p.pk for p in results}) == 1
    assert Payment.objects.filter(kind="refund").count() == 1
    inv.refresh_from_db()
    assert inv.total_paid == 0 and ledger_balance(customer.pk) == 140


@pytest.mark.django_db(transaction=True)
def test_concurrent_supplier_payment_retry_records_exactly_once(manager, supplier, product):
    inv = supplier_bill(manager, supplier, product)
    results = concurrent_calls(
        lambda: purchasing.pay_supplier_invoice(
            inv.pk,
            amount="500",
            method="cash",
            paid_on=timezone.localdate(),
            user=manager,
            idempotency_key="supplier-race",
        )
    )
    assert len({p.pk for p in results}) == 1
    inv.refresh_from_db()
    assert inv.paid_amount == 500 and SupplierPayment.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_duplicate_sale_retry_succeeds_after_last_stock_is_sold(manager, customer, make_product):
    product = make_product(stock=2)
    results = concurrent_calls(
        lambda: create_invoice(
            user=manager,
            customer=customer,
            lines=[{"product_id": product.pk, "quantity": 2}],
            submission_token="last-stock-retry",
        )
    )
    assert len({inv.pk for inv, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert Invoice.objects.count() == 1
    assert reconcile() == []
