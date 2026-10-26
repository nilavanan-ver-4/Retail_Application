from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import log as audit_log
from apps.core.exceptions import BusinessRuleError
from apps.core.models import DocumentSequence
from apps.core.money import ZERO
from apps.customers.services import post_ledger_entry
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement, lock_products
from apps.payments.models import Payment
from apps.payments.services import issue_refund, lock_invoice, recalculate_invoice
from apps.sales.calculations import refund_value
from apps.sales.models import Invoice, InvoiceLine

from .models import SalesReturn, SalesReturnLine

ACTIVE = (SalesReturn.Status.PENDING, SalesReturn.Status.APPROVED)


def returned_quantity(invoice_line_id, include_pending=True):
    statuses = ACTIVE if include_pending else (SalesReturn.Status.APPROVED,)
    return (
        SalesReturnLine.objects.filter(
            invoice_line_id=invoice_line_id, sales_return__status__in=statuses
        ).aggregate(s=Sum("quantity"))["s"]
        or 0
    )


@transaction.atomic
def request_return(*, invoice_id, lines, reason, user, return_date=None):
    """``lines``: [{"invoice_line_id", "quantity", "disposition"}]. Validates
    against the ORIGINAL sale: invoice must be valid and the cumulative returned
    quantity (including other pending returns) cannot exceed what was sold."""
    if not reason.strip():
        raise BusinessRuleError("A reason is required.")
    invoice = lock_invoice(invoice_id)
    if invoice.status == Invoice.Status.CANCELLED:
        raise BusinessRuleError("A cancelled invoice cannot be returned.")
    lines = [ln for ln in lines if ln["quantity"]]
    if not lines:
        raise BusinessRuleError("Select at least one item to return.")
    ret = SalesReturn.objects.create(
        number=f"RET-{DocumentSequence.next_value('return'):06d}",
        invoice=invoice,
        return_date=return_date or timezone.localdate(),
        reason=reason,
        requested_by=user,
    )
    for ln in lines:
        il = InvoiceLine.objects.select_for_update().get(pk=ln["invoice_line_id"], invoice=invoice)
        qty = int(ln["quantity"])
        if qty <= 0:
            raise BusinessRuleError("Return quantities must be positive.")
        already = returned_quantity(il.pk)
        if already + qty > il.quantity:
            raise BusinessRuleError(
                f"{il.description}: only {il.quantity - already} unit(s) can still be returned."
            )
        if ln["disposition"] not in SalesReturnLine.Disposition.values:
            raise BusinessRuleError("Choose resellable or damaged for every returned item.")
        SalesReturnLine.objects.create(
            sales_return=ret, invoice_line=il, quantity=qty, disposition=ln["disposition"]
        )
    audit_log("return.requested", ret, invoice=invoice.number)
    return ret


@transaction.atomic
def approve_return(return_id, *, user, refund_method=None, note=""):
    """Process the return: stock in (resellable) or write-off (damaged), credit the
    invoice, and refund money if the customer has already paid more than the new total."""
    ret = SalesReturn.objects.select_for_update().get(pk=return_id)
    if ret.status != SalesReturn.Status.PENDING:
        raise BusinessRuleError("This return was already processed.")
    invoice = lock_invoice(ret.invoice_id)
    if invoice.status == Invoice.Status.CANCELLED:
        raise BusinessRuleError("The invoice was cancelled; reject this return instead.")
    lines = list(ret.lines.select_related("invoice_line__product").order_by("invoice_line__product_id", "id"))
    products = lock_products([ln.invoice_line.product_id for ln in lines])
    credit_total = ZERO
    for ln in lines:
        il = InvoiceLine.objects.select_for_update().get(pk=ln.invoice_line_id)
        done_qty = returned_quantity(il.pk, include_pending=False)
        done_value = (
            SalesReturnLine.objects.filter(
                invoice_line=il, sales_return__status=SalesReturn.Status.APPROVED
            ).aggregate(s=Sum("credit_amount"))["s"]
            or ZERO
        )
        value = refund_value(il.line_total, il.quantity, ln.quantity, done_qty, done_value)
        ln.credit_amount = value
        ln.save(update_fields=["credit_amount"])
        credit_total += value
        pid = il.product_id
        apply_movement(
            pid,
            StockMovement.Type.SALE_RETURN,
            ln.quantity,
            user=user,
            reference=("sales_return", ret.pk),
            locked_product=products[pid],
            note=f"Return {ret.number}",
        )
        if ln.disposition == SalesReturnLine.Disposition.DAMAGED:
            # received then written off: the ledger shows the goods came back AND were scrapped
            apply_movement(
                pid,
                StockMovement.Type.RETURN_DAMAGED,
                -ln.quantity,
                user=user,
                reference=("sales_return", ret.pk),
                locked_product=products[pid],
                note=f"Damaged on return {ret.number}",
            )
    invoice.returned_total = invoice.returned_total + credit_total
    invoice.save(update_fields=["returned_total", "updated_at"])
    if invoice.customer_id and credit_total:
        post_ledger_entry(
            invoice.customer_id,
            "Credit",
            credit_total,
            user=user,
            invoice=invoice,
            description=f"Return credit {ret.number} - Invoice #{invoice.number}",
        )
    # money owed back = what the customer paid beyond the invoice's new effective total
    refund_due = max(ZERO, invoice.total_paid - invoice.effective_total)
    refunded = ZERO
    if refund_due > 0:
        if refund_method not in Payment.Method.values:
            raise BusinessRuleError(f"Choose how the refund of {refund_due} will be paid.")
        issue_refund(
            invoice.pk,
            amount=refund_due,
            method=refund_method,
            user=user,
            invoice=invoice,
            reference=ret.number,
            idempotency_key=f"refund-{ret.number}",
        )
        refunded = refund_due
    recalculate_invoice(invoice)
    ret.status = SalesReturn.Status.APPROVED
    ret.credit_amount = credit_total
    ret.refund_amount = refunded
    ret.refund_method = refund_method or ""
    ret.decided_by, ret.decided_at, ret.decision_note = user, timezone.now(), note[:255]
    ret.save()
    audit_log("return.approved", ret, credit=str(credit_total), refund=str(refunded), invoice=invoice.number)
    return ret


@transaction.atomic
def reject_return(return_id, *, user, note):
    ret = SalesReturn.objects.select_for_update().get(pk=return_id)
    if ret.status != SalesReturn.Status.PENDING:
        raise BusinessRuleError("This return was already processed.")
    ret.status = SalesReturn.Status.REJECTED
    ret.decided_by, ret.decided_at, ret.decision_note = user, timezone.now(), note[:255]
    ret.save()
    audit_log("return.rejected", ret, note=note)
    return ret


def refundable_summary(invoice):
    """Rows for the return form: sold, already returned, still returnable."""
    rows = []
    for il in invoice.lines.select_related("product"):
        rows.append(
            {
                "line": il,
                "returned": returned_quantity(il.pk),
                "available": il.quantity - returned_quantity(il.pk),
            }
        )
    return rows
