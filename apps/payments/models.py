from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class Payment(TimeStampedModel):
    """A single money movement against an invoice. Split payments are several
    rows. Only COMPLETED rows count toward ``Invoice.total_paid``. No card
    numbers, CVV or other prohibited card data are ever stored - only a
    free-text reference (e.g. terminal slip / bank ref / UPI txn id)."""

    class Kind(models.TextChoices):
        PAYMENT = "payment", "Payment"
        REFUND = "refund", "Refund"

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "credit_card", "Card"
        BANK = "bank_transfer", "Bank transfer"
        UPI = "upi", "UPI"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PENDING = "pending", "Pending (legacy placeholder)"
        REVERSED = "reversed", "Reversed"

    invoice = models.ForeignKey("sales.Invoice", on_delete=models.PROTECT, related_name="payments")
    customer = models.ForeignKey("customers.Customer", null=True, on_delete=models.PROTECT,
                                 related_name="payments")
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.PAYMENT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices)
    legacy_mode = models.CharField(max_length=50, blank=True, editable=False)
    reference = models.CharField(max_length=100, blank=True)
    receipt_no = models.CharField(max_length=100, blank=True)
    paid_on = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey("accounts.User", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    reverse_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "payments_payment"
        ordering = ["-paid_on", "-id"]
        permissions = [("reverse_payment", "Can reverse payments"),
                       ("view_refund", "Can view refunds")]
        indexes = [models.Index(fields=["paid_on", "method"]),
                   models.Index(fields=["invoice", "status"])]
        constraints = [models.CheckConstraint(condition=Q(amount__gte=Decimal("0")),
                                              name="payment_amount_gte_0")]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount} on {self.invoice_id}"
