"""Purchasing workflows: PO -> goods receipt (stock in) -> supplier invoice -> payment."""

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone

from apps.audit.services import log as audit_log
from apps.core.exceptions import BusinessRuleError
from apps.core.models import DocumentSequence
from apps.core.money import q2, to_decimal
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement
from apps.products.models import Product

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
)


def _validated_lines(lines, *, order=False):
    if not lines:
        raise BusinessRuleError("Add at least one product line.")
    result, seen = [], set()
    for source in lines:
        ln = dict(source)
        qty = to_decimal(ln["quantity"])
        if qty is None or qty != qty.to_integral_value() or not 0 < qty <= 2147483647:
            raise BusinessRuleError("Quantities must be positive whole numbers.")
        ln["quantity"] = int(qty)
        ln["unit_cost"] = q2(ln["unit_cost"])
        if ln["unit_cost"] < 0:
            raise BusinessRuleError("Unit cost cannot be negative.")
        if not ln["product"].is_active:
            raise BusinessRuleError("Archived products cannot be purchased.")
        if order:
            if ln["product"].pk in seen:
                raise BusinessRuleError("Use one line per product on a purchase order.")
            seen.add(ln["product"].pk)
            ln["discount"] = q2(ln.get("discount", 0))
            rate = to_decimal(ln.get("tax_rate", 0))
            if rate is None or not 0 <= rate <= 100:
                raise BusinessRuleError("Tax rate must be between 0 and 100.")
            ln["tax_rate"] = rate
            if not 0 <= ln["discount"] <= ln["unit_cost"] * ln["quantity"]:
                raise BusinessRuleError("Line discount must be within the line amount.")
        result.append(ln)
    return result


def _validate_supplier(supplier, purchase_order=None, receipt=None):
    if not supplier.is_active:
        raise BusinessRuleError("This supplier is inactive.")
    if purchase_order and purchase_order.supplier_id != supplier.pk:
        raise BusinessRuleError("The purchase order belongs to a different supplier.")
    if receipt:
        if receipt.supplier_id != supplier.pk:
            raise BusinessRuleError("The goods receipt belongs to a different supplier.")
        if receipt.status != GoodsReceipt.Status.CONFIRMED:
            raise BusinessRuleError("Confirm the goods receipt before recording its supplier invoice.")
        if purchase_order and receipt.purchase_order_id != purchase_order.pk:
            raise BusinessRuleError("The goods receipt belongs to a different purchase order.")


def _validate_receipt_line(ln, purchase_order):
    pol = ln.get("po_line")
    if purchase_order:
        if not pol or pol.order_id != purchase_order.pk or pol.product_id != ln["product"].pk:
            raise BusinessRuleError("Every receipt line must match a product line on its purchase order.")
    elif pol:
        raise BusinessRuleError("A purchase order is required for linked receipt lines.")


def next_number(prefix, name=None, width=6):
    return f"{prefix}-{DocumentSequence.next_value(name or prefix):0{width}d}"


def next_supplier_invoice_number(year=None):
    """Legacy format: YYYY + 5-digit counter, e.g. 202600001."""
    year = year or timezone.localdate().year
    return f"{year}{DocumentSequence.next_value(f'supplier_invoice-{year}'):05d}"


@transaction.atomic
def create_purchase_order(
    *,
    supplier,
    order_date,
    expected_date,
    lines,
    user,
    notes="",
    discount=Decimal("0.00"),
    status=PurchaseOrder.Status.ORDERED,
):
    lines = _validated_lines(lines, order=True)
    _validate_supplier(supplier)
    discount = q2(discount)
    subtotal = sum((ln["unit_cost"] * ln["quantity"] - ln["discount"] for ln in lines), Decimal(0))
    if not 0 <= discount <= subtotal:
        raise BusinessRuleError("Order discount must be within the subtotal.")
    if status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.ORDERED):
        raise BusinessRuleError("New purchase orders must be draft or ordered.")
    po = PurchaseOrder.objects.create(
        number=next_number("PO"),
        supplier=supplier,
        order_date=order_date,
        expected_date=expected_date,
        notes=notes,
        discount=discount,
        status=status,
        created_by=user,
    )
    for ln in lines:
        PurchaseOrderLine.objects.create(order=po, **ln)
    audit_log("po.created", po, total=str(po.total))
    return po


@transaction.atomic
def create_receipt(
    *, supplier, receipt_date, lines, user, purchase_order=None, delivery_note="", comments=""
):
    """Create a DRAFT receipt. Stock is NOT touched until ``confirm_receipt``."""
    lines = _validated_lines(lines)
    _validate_supplier(supplier, purchase_order)
    for ln in lines:
        _validate_receipt_line(ln, purchase_order)
    receipt = GoodsReceipt.objects.create(
        number=next_number("GRN"),
        supplier=supplier,
        purchase_order=purchase_order,
        receipt_date=receipt_date,
        delivery_note=delivery_note,
        comments=comments,
        created_by=user,
    )
    for ln in lines:
        GoodsReceiptLine.objects.create(receipt=receipt, **ln)
    return receipt


@transaction.atomic
def confirm_receipt(receipt_id, user):
    """Post stock for a draft receipt. Idempotent-safe: a second confirmation is
    rejected under the row lock, so double clicks / retries cannot double-count."""
    receipt = GoodsReceipt.objects.select_for_update().get(pk=receipt_id)
    if receipt.status == GoodsReceipt.Status.CONFIRMED:
        raise BusinessRuleError(f"Receipt {receipt.number} is already confirmed.")
    lines = list(receipt.lines.select_related("product", "po_line").order_by("product_id", "id"))
    if not lines:
        raise BusinessRuleError("Receipt has no lines.")
    po = None
    if receipt.purchase_order_id:
        po = PurchaseOrder.objects.select_for_update().get(pk=receipt.purchase_order_id)
        if po.status in (PurchaseOrder.Status.CANCELLED, PurchaseOrder.Status.DRAFT):
            raise BusinessRuleError(
                f"Purchase order {po.number} is {po.get_status_display().lower()} and cannot be received."
            )
    _validate_supplier(receipt.supplier, po)
    for ln in lines:
        _validated_lines([{"product": ln.product, "quantity": ln.quantity, "unit_cost": ln.unit_cost}])
        _validate_receipt_line({"product": ln.product, "po_line": ln.po_line}, po)
    for ln in lines:
        if ln.po_line_id:
            pol = PurchaseOrderLine.objects.select_for_update().get(pk=ln.po_line_id)
            if ln.quantity > pol.outstanding:
                raise BusinessRuleError(
                    f"{ln.product.name}: receiving {ln.quantity} exceeds outstanding {pol.outstanding}."
                )
            PurchaseOrderLine.objects.filter(pk=pol.pk).update(
                received_quantity=F("received_quantity") + ln.quantity
            )
        movement = apply_movement(
            ln.product_id,
            StockMovement.Type.PURCHASE,
            ln.quantity,
            user=user,
            reference=("goods_receipt", receipt.pk),
            unit_cost=ln.unit_cost,
            note=f"Receipt {receipt.number}",
        )
        product = Product.objects.select_for_update().get(pk=ln.product_id)
        if q2(ln.unit_cost) != product.cost_price:
            old = product.cost_price
            product.cost_price = q2(ln.unit_cost)  # costing method: latest purchase cost
            product.save(update_fields=["cost_price", "updated_at"])
            audit_log(
                "product.cost_changed",
                product,
                old=str(old),
                new=str(product.cost_price),
                reason=f"goods receipt {receipt.number}",
            )
        del movement
    receipt.status = GoodsReceipt.Status.CONFIRMED
    receipt.confirmed_at = timezone.now()
    receipt.confirmed_by = user
    receipt.save(update_fields=["status", "confirmed_at", "confirmed_by", "updated_at"])
    if po:
        outstanding = po.lines.filter(received_quantity__lt=F("quantity")).exists()
        po.status = PurchaseOrder.Status.PARTIAL if outstanding else PurchaseOrder.Status.RECEIVED
        po.save(update_fields=["status", "updated_at"])
    audit_log("receipt.confirmed", receipt, lines=len(lines))
    return receipt


@transaction.atomic
def record_supplier_invoice(
    *,
    supplier,
    invoice_number,
    invoice_date,
    due_date,
    lines,
    user,
    receipt=None,
    purchase_order=None,
    notes="",
):
    """Record the supplier's bill. Does not move stock and does not pay anything."""
    lines = _validated_lines(lines)
    if receipt:
        receipt = GoodsReceipt.objects.select_for_update().get(pk=receipt.pk)
    purchase_order = purchase_order or (receipt.purchase_order if receipt else None)
    _validate_supplier(supplier, purchase_order, receipt)
    if invoice_number and SupplierInvoice.objects.filter(invoice_number=invoice_number).exists():
        raise BusinessRuleError("A supplier invoice with this number already exists.")
    total = q2(sum((Decimal(ln["unit_cost"]) * ln["quantity"] for ln in lines), Decimal(0)))
    inv = SupplierInvoice.objects.create(
        invoice_number=invoice_number or next_supplier_invoice_number(),
        supplier=supplier,
        invoice_date=invoice_date,
        due_date=due_date,
        total_amount=total,
        receipt=receipt,
        purchase_order=purchase_order,
        notes=notes,
        created_by=user,
    )
    for ln in lines:
        SupplierInvoiceLine.objects.create(invoice=inv, **ln)
    audit_log("supplier_invoice.recorded", inv, total=str(total))
    return inv


@transaction.atomic
def quick_receive(*, product, quantity, user, unit_cost=None, comments=""):
    """Legacy ``add_item`` flow: add stock for one product and book the supplier
    invoice in a single step (receipt confirmed + invoice Pending/Unpaid)."""
    if not product.supplier_id:
        raise BusinessRuleError("This product has no supplier; assign one before receiving stock.")
    cost = q2(unit_cost if unit_cost is not None else product.cost_price)
    today = timezone.localdate()
    receipt = create_receipt(
        supplier=product.supplier,
        receipt_date=today,
        user=user,
        comments=comments,
        lines=[{"product": product, "quantity": quantity, "unit_cost": cost}],
    )
    confirm_receipt(receipt.pk, user)
    inv = record_supplier_invoice(
        supplier=product.supplier,
        invoice_number=None,
        invoice_date=today,
        due_date=None,
        receipt=receipt,
        user=user,
        lines=[{"product": product, "quantity": quantity, "unit_cost": cost}],
    )
    return receipt, inv


@transaction.atomic
def pay_supplier_invoice(invoice_id, *, amount, method, paid_on, user, reference="", idempotency_key=None):
    amount = q2(amount)
    inv = SupplierInvoice.objects.select_for_update().get(pk=invoice_id)
    if method not in SupplierPayment.Method.values:
        raise BusinessRuleError("Unknown payment method.")
    if idempotency_key:
        existing = SupplierPayment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            if (
                existing.invoice_id,
                existing.amount,
                existing.method,
                existing.reference,
                existing.paid_on,
            ) != (inv.pk, amount, method, reference, paid_on):
                raise BusinessRuleError("This payment token was already used for a different operation.")
            return existing
    if inv.status == SupplierInvoice.Status.CANCELLED:
        raise BusinessRuleError("Cannot pay a cancelled supplier invoice.")
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    if amount > inv.balance:
        raise BusinessRuleError(f"Payment {amount} exceeds the outstanding balance {inv.balance}.")
    try:
        with transaction.atomic():
            payment = SupplierPayment.objects.create(
                invoice=inv,
                amount=amount,
                method=method,
                reference=reference,
                paid_on=paid_on,
                idempotency_key=idempotency_key or None,
                created_by=user,
            )
    except IntegrityError:
        if idempotency_key and SupplierPayment.objects.filter(idempotency_key=idempotency_key).exists():
            raise BusinessRuleError(
                "This payment token was already used for a different operation."
            ) from None
        raise
    inv.paid_amount = inv.paid_amount + amount
    inv.payment_status = (
        SupplierInvoice.PaymentStatus.PAID
        if inv.paid_amount >= inv.total_amount
        else SupplierInvoice.PaymentStatus.PARTIAL
    )
    if inv.status == SupplierInvoice.Status.PENDING:
        inv.status = SupplierInvoice.Status.CONFIRMED
    inv.save(update_fields=["paid_amount", "payment_status", "status", "updated_at"])
    audit_log("supplier_payment.recorded", payment, invoice=inv.invoice_number, amount=str(amount))
    return payment
