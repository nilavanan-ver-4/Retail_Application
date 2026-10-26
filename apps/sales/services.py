"""Invoice creation and cancellation.

The server is the only authority for money: it ignores any totals the browser
computed, takes prices from the catalogue (unless the user holds the
``override_price`` permission), recomputes every figure with ``calculations``,
validates stock under row locks, and writes invoice, lines, stock movements,
ledger entries and payments in ONE transaction. If anything fails, nothing is
written.
"""

import logging
from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.services import log as audit_log
from apps.core.exceptions import BusinessRuleError, InsufficientStock
from apps.core.models import DocumentSequence
from apps.core.money import ZERO, q2
from apps.customers.models import Customer
from apps.customers.services import outstanding_for, post_ledger_entry
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement, lock_products
from apps.payments.services import lock_invoice, recalculate_invoice, record_payment

from .calculations import LineInput, compute_invoice
from .models import Invoice, InvoiceLine

logger = logging.getLogger("retail")
INVOICE_SEQ = "invoice"


def _next_invoice_number():
    DocumentSequence.ensure_at_least(INVOICE_SEQ, settings.INVOICE_NUMBER_START - 1)
    return str(DocumentSequence.next_value(INVOICE_SEQ))


def _resolve_customer(customer, mobile, name, address, email):
    if customer is not None:
        return customer
    if not mobile or not name:
        raise BusinessRuleError("Customer mobile and name are required.")
    cust, created = Customer.objects.get_or_create(
        mobile=mobile, defaults={"name": name, "billing_address": address or "", "email": email or ""}
    )
    if created:
        audit_log("customer.created", cust, via="invoice")
    return cust


@transaction.atomic
def create_invoice(
    *,
    user,
    lines,
    invoice_date=None,
    customer=None,
    customer_mobile=None,
    customer_name=None,
    customer_address="",
    customer_email="",
    terms="",
    sold_by="",
    discount_percent=ZERO,
    discount_amount=None,
    shipping_charge=ZERO,
    payments=(),
    submission_token=None,
    can_override_price=False,
    can_override_credit=False,
):
    """Create a confirmed invoice. Returns ``(invoice, created)``; ``created`` is
    False when ``submission_token`` was already used (duplicate submission)."""
    if submission_token:
        existing = Invoice.objects.filter(submission_token=submission_token).first()
        if existing:
            return existing, False
    if not lines:
        raise BusinessRuleError("Add at least one product to the invoice.")

    # --- lock & validate products (deterministic order => no deadlocks) -----
    wanted = defaultdict(int)
    for ln in lines:
        qty = ln["quantity"]
        if int(qty) != qty or qty <= 0:
            raise BusinessRuleError("Quantities must be positive whole numbers.")
        wanted[ln["product_id"]] += int(qty)
    products = lock_products(wanted)
    missing = set(wanted) - set(products)
    if missing:
        raise BusinessRuleError("One or more products no longer exist.")
    for pid, qty in wanted.items():
        p = products[pid]
        if not p.is_active:
            raise BusinessRuleError(f"{p.name} is archived and cannot be sold.")
        if qty > p.stock_on_hand and not p.allow_negative_stock:
            raise InsufficientStock(p, p.stock_on_hand, qty)

    # --- price & tax come from the catalogue, never from the browser --------
    inputs, overrides = [], []
    for ln in lines:
        p = products[ln["product_id"]]
        requested = ln.get("unit_price")
        price = p.selling_price
        if requested is not None and Decimal(requested) != p.selling_price:
            if not can_override_price:
                raise BusinessRuleError(f"{p.name}: you are not allowed to change the catalogue price.")
            price = Decimal(requested)
            overrides.append((p, p.selling_price, price))
        inputs.append(
            LineInput(
                quantity=int(ln["quantity"]),
                unit_price=price,
                discount=Decimal(ln.get("discount") or 0),
                tax_rate=p.tax_rate,
            )
        )
    totals = compute_invoice(
        inputs,
        discount_percent=Decimal(discount_percent or 0),
        discount_amount=discount_amount,
        shipping=shipping_charge,
    )
    if totals.total <= 0:
        raise BusinessRuleError("Invoice total must be greater than zero.")

    # --- payments validated before anything is written ----------------------
    pay_total = q2(sum((Decimal(p["amount"]) for p in payments), ZERO))
    if any(Decimal(p["amount"]) <= 0 for p in payments):
        raise BusinessRuleError("Every payment amount must be greater than zero.")
    if pay_total > totals.total:
        raise BusinessRuleError(f"Payments ({pay_total}) exceed the invoice total ({totals.total}).")

    cust = _resolve_customer(customer, customer_mobile, customer_name, customer_address, customer_email)
    if not cust.is_active:
        raise BusinessRuleError(f"Customer {cust.name} is inactive.")
    unpaid = totals.total - pay_total
    if cust.credit_limit is not None and unpaid > 0 and not can_override_credit:
        Customer.objects.select_for_update().get(pk=cust.pk)
        if outstanding_for(cust.pk) + unpaid > cust.credit_limit:
            raise BusinessRuleError(
                f"This sale would exceed {cust.name}'s credit limit of {cust.credit_limit}."
            )

    # --- write ---------------------------------------------------------------
    invoice = Invoice(
        number=_next_invoice_number(),
        customer=cust,
        customer_name=cust.name,
        customer_mobile=cust.mobile,
        billing_address=cust.billing_address,
        date=invoice_date or timezone.localdate(),
        terms=terms,
        sold_by=sold_by or user.display_name(),
        created_by=user,
        subtotal=totals.subtotal,
        discount_percent=totals.discount_percent,
        discount_amount=totals.discount_amount,
        shipping_charge=totals.shipping,
        tax_total=totals.tax_total,
        total=totals.total,
        submission_token=submission_token or None,
    )
    try:
        with transaction.atomic():  # savepoint: a duplicate token must not poison the outer tx
            invoice.save()
    except IntegrityError:
        existing = (
            Invoice.objects.filter(submission_token=submission_token).first() if submission_token else None
        )
        if existing:
            return existing, False
        raise
    for ln, res in zip(lines, totals.lines, strict=True):
        p = products[ln["product_id"]]
        InvoiceLine.objects.create(
            invoice=invoice,
            product=p,
            description=p.name,
            unit=p.unit,
            quantity=res.quantity,
            unit_price=res.unit_price,
            discount=res.discount,
            net_amount=res.net,
            allocated_discount=res.allocated_discount,
            taxable_amount=res.taxable,
            tax_rate=res.tax_rate,
            tax_amount=res.tax,
            line_total=res.total,
            unit_cost=p.cost_price,
        )
    for pid, qty in sorted(wanted.items()):
        apply_movement(
            pid,
            StockMovement.Type.SALE,
            -qty,
            user=user,
            reference=("invoice", invoice.pk),
            locked_product=products[pid],
            unit_cost=products[pid].cost_price,
            note=f"Invoice {invoice.number}",
        )
    post_ledger_entry(
        cust.pk,
        "Debit",
        totals.total,
        user=user,
        invoice=invoice,
        description=f"Invoice Creation - {cust.name} (Invoice #{invoice.number})",
    )
    for pay in payments:
        record_payment(
            invoice.pk,
            amount=pay["amount"],
            method=pay["method"],
            user=user,
            reference=pay.get("reference", ""),
            invoice=invoice,
            paid_on=invoice.date,
        )
    recalculate_invoice(invoice)
    for p, old, new in overrides:
        audit_log("invoice.price_override", invoice, product=p.sku, catalogue=str(old), charged=str(new))
    audit_log(
        "invoice.created", invoice, total=str(invoice.total), lines=len(lines), paid=str(invoice.total_paid)
    )
    return invoice, True


@transaction.atomic
def cancel_invoice(invoice_id, *, user, reason):
    """Cancel an invoice: restores stock and reverses the ledger debit.

    Rules: a reason is mandatory; money must already have been returned
    (reverse/refund payments first); invoices with processed returns cannot be
    cancelled (the credit note already adjusted them).
    """
    if not reason.strip():
        raise BusinessRuleError("A reason is required to cancel an invoice.")
    invoice = lock_invoice(invoice_id)
    if invoice.status == Invoice.Status.CANCELLED:
        raise BusinessRuleError("Invoice is already cancelled.")
    if invoice.returned_total > 0:
        raise BusinessRuleError("This invoice has returns; it cannot be cancelled.")
    if invoice.total_paid > 0:
        raise BusinessRuleError("Reverse or refund the payments received before cancelling.")
    lines = list(invoice.lines.select_related("product").order_by("product_id"))
    wanted = defaultdict(int)
    for ln in lines:
        wanted[ln.product_id] += ln.quantity
    products = lock_products(wanted)
    for pid, qty in sorted(wanted.items()):
        apply_movement(
            pid,
            StockMovement.Type.SALE_CANCEL,
            qty,
            user=user,
            reference=("invoice", invoice.pk),
            locked_product=products[pid],
            note=f"Cancelled invoice {invoice.number}",
        )
    if invoice.customer_id:
        post_ledger_entry(
            invoice.customer_id,
            "Credit",
            invoice.total,
            user=user,
            invoice=invoice,
            description=f"Invoice cancelled - Invoice #{invoice.number}: {reason}",
        )
    invoice.status = Invoice.Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.cancelled_by = user
    invoice.cancel_reason = reason[:255]
    invoice.save(update_fields=["status", "cancelled_at", "cancelled_by", "cancel_reason", "updated_at"])
    audit_log("invoice.cancelled", invoice, reason=reason, total=str(invoice.total))
    return invoice
