from decimal import Decimal

from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class SalesReturn(TimeStampedModel):
    """A customer return / credit note against one invoice. A cashier raises the
    request (PENDING); a user with ``approve_return`` processes it (APPROVED),
    which moves stock and money in one transaction."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved & processed"
        REJECTED = "rejected", "Rejected"

    number = models.CharField(max_length=30, unique=True)
    invoice = models.ForeignKey("sales.Invoice", on_delete=models.PROTECT, related_name="returns")
    return_date = models.DateField()
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    credit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                        editable=False, help_text="Value credited to the invoice")
    refund_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                        editable=False, help_text="Cash paid back")
    refund_method = models.CharField(max_length=20, blank=True)
    requested_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                     related_name="+")
    decided_by = models.ForeignKey("accounts.User", null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "returns_salesreturn"
        ordering = ["-return_date", "-id"]
        permissions = [("approve_return", "Can approve or reject sales returns")]
        constraints = [models.CheckConstraint(condition=Q(credit_amount__gte=0) & Q(refund_amount__gte=0),
                                              name="return_amounts_gte_0")]

    def __str__(self):
        return self.number

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("returns:detail", args=[self.pk])


class SalesReturnLine(models.Model):
    class Disposition(models.TextChoices):
        RESELLABLE = "resellable", "Resellable - back to stock"
        DAMAGED = "damaged", "Damaged / unusable - write off"

    sales_return = models.ForeignKey(SalesReturn, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey("sales.InvoiceLine", on_delete=models.PROTECT,
                                     related_name="return_lines")
    quantity = models.PositiveIntegerField()
    disposition = models.CharField(max_length=12, choices=Disposition.choices)
    credit_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                        editable=False)

    class Meta:
        db_table = "returns_salesreturnline"
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=1),
                                              name="retline_quantity_gte_1")]
