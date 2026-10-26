"""Payment, reversal and refund operations.

Every function locks the invoice row first, so concurrent submissions for the
same invoice are serialised and cannot overpay it. ``idempotency_key`` makes a
retried request (double click, network retry) return the original payment
instead of recording a second one.
"""

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.audit.services import log as audit_log
from apps.core.exceptions import BusinessRuleError
from apps.core.money import ZERO, q2
from apps.customers.services import post_ledger_entry
from apps.sales.models import Invoice

from .models import Payment


def lock_invoice(invoice_id) -> Invoice:
    return Invoice.objects.select_for_update().get(pk=invoice_id)


def recalculate_invoice(invoice: Invoice) -> Invoice:
    """Recompute ``total_paid``, ``payment_status`` and ``status`` from the
    payment rows (single source of truth). Caller holds the invoice lock."""
    paid = Payment.objects.filter(invoice=invoice, kind=Payment.Kind.PAYMENT,
                                  status=Payment.Status.COMPLETED).aggregate(s=Sum("amount"))["s"] or ZERO
    refunded = Payment.objects.filter(invoice=invoice, kind=Payment.Kind.REFUND,
                                      status=Payment.Status.COMPLETED).aggregate(s=Sum("amount"))["s"] or ZERO
    invoice.total_paid = paid - refunded
    if invoice.status != Invoice.Status.CANCELLED:
        effective = invoice.total - invoice.returned_total
        # shipping is not refundable, so "fully returned" = all goods value credited
        if invoice.returned_total > 0 and invoice.returned_total >= invoice.total - invoice.shipping_charge:
            invoice.status = Invoice.Status.RETURNED
            invoice.payment_status = Invoice.PaymentStatus.PAID
        else:
            if invoice.total_paid >= effective:
                invoice.payment_status = Invoice.PaymentStatus.PAID
            elif invoice.total_paid > 0:
                invoice.payment_status = Invoice.PaymentStatus.PARTIAL
            else:
                invoice.payment_status = Invoice.PaymentStatus.UNPAID
            invoice.status = (Invoice.Status.COMPLETED
                              if invoice.payment_status == Invoice.PaymentStatus.PAID
                              else Invoice.Status.PENDING)
    invoice.save(update_fields=["total_paid", "payment_status", "status", "updated_at"])
    return invoice


def _next_receipt_no():
    from apps.core.models import DocumentSequence
    return f"RCT-{DocumentSequence.next_value('receipt'):06d}"


@transaction.atomic
def record_payment(invoice_id, *, amount, method, user, paid_on=None, reference="",
                   idempotency_key=None, notes="", invoice=None):
    """Record a customer payment. ``invoice`` may be passed when the caller already
    holds the lock (invoice creation)."""
    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing, False
    amount = q2(amount)
    if amount <= 0:
        raise BusinessRuleError("Payment amount must be greater than zero.")
    if method not in Payment.Method.values:
        raise BusinessRuleError("Unknown payment method.")
    invoice = invoice or lock_invoice(invoice_id)
    if invoice.status in (Invoice.Status.CANCELLED, Invoice.Status.RETURNED):
        raise BusinessRuleError(f"Invoice {invoice.number} is {invoice.get_status_display().lower()}; "
                                "it cannot take payments.")
    if amount > invoice.balance_due:
        raise BusinessRuleError(
            f"Payment {amount} exceeds the outstanding balance {invoice.balance_due}.")
    payment = Payment.objects.create(
        invoice=invoice, customer=invoice.customer, kind=Payment.Kind.PAYMENT, amount=amount,
        method=method, reference=reference[:100], receipt_no=_next_receipt_no(),
        paid_on=paid_on or timezone.localdate(), idempotency_key=idempotency_key or None,
        notes=notes[:255], created_by=user)
    if invoice.customer_id:
        post_ledger_entry(invoice.customer_id, "Credit", amount, user=user, invoice=invoice,
                          description=f"Payment received ({payment.get_method_display()}) "
                                      f"- Invoice #{invoice.number}")
    recalculate_invoice(invoice)
    audit_log("payment.recorded", payment, invoice=invoice.number, amount=str(amount), method=method)
    return payment, True


@transaction.atomic
def reverse_payment(payment_id, *, user, reason):
    if not reason.strip():
        raise BusinessRuleError("A reason is required to reverse a payment.")
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    invoice = lock_invoice(payment.invoice_id)
    if payment.status != Payment.Status.COMPLETED:
        raise BusinessRuleError("Only completed payments can be reversed.")
    payment.status = Payment.Status.REVERSED
    payment.reversed_at = timezone.now()
    payment.reversed_by = user
    payment.reverse_reason = reason[:255]
    payment.save(update_fields=["status", "reversed_at", "reversed_by", "reverse_reason", "updated_at"])
    if invoice.customer_id and payment.amount:
        # reversing a payment re-opens the debt; reversing a refund cancels the payout
        post_ledger_entry(invoice.customer_id, "Debit" if payment.kind == Payment.Kind.PAYMENT else "Credit",
                          payment.amount, user=user, invoice=invoice,
                          description=f"Reversal of {payment.get_kind_display().lower()} "
                                      f"{payment.receipt_no or payment.pk}: {reason}")
    recalculate_invoice(invoice)
    audit_log("payment.reversed", payment, reason=reason, amount=str(payment.amount),
              invoice=invoice.number)
    return payment


@transaction.atomic
def issue_refund(invoice_id, *, amount, method, user, reference="", notes="", invoice=None,
                 idempotency_key=None):
    """Pay money back to the customer. Cannot exceed what the invoice has net received."""
    if idempotency_key:
        existing = Payment.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
    amount = q2(amount)
    if amount <= 0:
        raise BusinessRuleError("Refund amount must be greater than zero.")
    invoice = invoice or lock_invoice(invoice_id)
    if amount > invoice.total_paid:
        raise BusinessRuleError(f"Refund {amount} exceeds the amount received ({invoice.total_paid}).")
    refund = Payment.objects.create(
        invoice=invoice, customer=invoice.customer, kind=Payment.Kind.REFUND, amount=amount,
        method=method, reference=reference[:100], receipt_no=_next_receipt_no(),
        paid_on=timezone.localdate(), notes=notes[:255], created_by=user,
        idempotency_key=idempotency_key or None)
    if invoice.customer_id:
        post_ledger_entry(invoice.customer_id, "Debit", amount, user=user, invoice=invoice,
                          description=f"Refund paid ({refund.get_method_display()}) "
                                      f"- Invoice #{invoice.number}")
    recalculate_invoice(invoice)
    audit_log("refund.issued", refund, invoice=invoice.number, amount=str(amount))
    return refund
