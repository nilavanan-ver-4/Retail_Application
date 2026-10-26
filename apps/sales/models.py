from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q

from apps.core.models import TimeStampedModel

ZERO = Decimal("0.00")


class Invoice(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        COMPLETED = "Completed", "Completed"
        RETURNED = "Returned", "Fully returned"
        CANCELLED = "Cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "Unpaid", "Unpaid"
        PARTIAL = "Partially Paid", "Partially paid"
        PAID = "Paid", "Paid"

    # ``id`` is the legacy ``invoice.invoice_no`` for migrated rows; ``number`` is the
    # printed document number (same value for legacy rows, sequence-allocated after).
    number = models.CharField(max_length=30, unique=True)
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.PROTECT, related_name="invoices", null=True
    )
    customer_name = models.CharField(max_length=255)
    customer_mobile = models.CharField(max_length=15)
    billing_address = models.TextField(blank=True)
    date = models.DateField()
    terms = models.TextField(blank=True)
    sold_by = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Sum of line amounts after line discounts (legacy total_amount)",
    )
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, validators=[MinValueValidator(ZERO)]
    )
    shipping_charge = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=12, decimal_places=2, help_text="Legacy final_amount")

    total_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        editable=False,
        help_text="Completed payments minus completed refunds",
    )
    returned_total = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID
    )

    submission_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=255, blank=True)
    is_legacy = models.BooleanField(default=False, editable=False)

    class Meta:
        db_table = "sales_invoice"
        ordering = ["-date", "-id"]
        permissions = [
            ("cancel_invoice", "Can cancel invoices"),
            ("override_price", "Can sell below/above the catalogue price"),
        ]
        indexes = [
            models.Index(fields=["date"]),
            models.Index(fields=["customer", "date"]),
            models.Index(fields=["status", "payment_status"]),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(subtotal__gte=0), name="invoice_subtotal_gte_0"),
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0) & Q(discount_amount__lte=F("subtotal")),
                name="invoice_discount_within_subtotal",
            ),
            models.CheckConstraint(condition=Q(total__gte=0), name="invoice_total_gte_0"),
            # the money invariant must hold even if application code is bypassed
            models.CheckConstraint(
                condition=Q(
                    total=F("subtotal") - F("discount_amount") + F("tax_total") + F("shipping_charge")
                ),
                name="invoice_total_equation",
            ),
            models.CheckConstraint(condition=Q(total_paid__gte=0), name="invoice_paid_gte_0"),
            models.CheckConstraint(
                condition=Q(returned_total__gte=0) & Q(returned_total__lte=F("total")),
                name="invoice_returned_within_total",
            ),
        ]

    def __str__(self):
        return f"Invoice {self.number}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("sales:detail", args=[self.pk])

    @property
    def effective_total(self):
        return self.total - self.returned_total

    @property
    def balance_due(self):
        return self.effective_total - self.total_paid

    @property
    def is_cancellable(self):
        return self.status in (self.Status.PENDING, self.Status.COMPLETED)


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="invoice_lines")
    description = models.CharField(max_length=200)  # product name at time of sale
    unit = models.CharField(max_length=50, blank=True)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    net_amount = models.DecimalField(
        max_digits=12, decimal_places=2, help_text="unit_price x quantity - discount (legacy total)"
    )
    allocated_discount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    taxable_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=ZERO)
    line_total = models.DecimalField(
        max_digits=12, decimal_places=2, default=ZERO, help_text="taxable_amount + tax_amount"
    )
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Cost snapshot at sale time, for profit estimates",
    )

    class Meta:
        db_table = "sales_invoiceline"
        ordering = ["id"]
        indexes = [models.Index(fields=["product"])]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gte=1), name="invline_quantity_gte_1"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="invline_price_gte_0"),
            models.CheckConstraint(condition=Q(discount__gte=0), name="invline_discount_gte_0"),
            models.CheckConstraint(
                condition=Q(net_amount=F("unit_price") * F("quantity") - F("discount")),
                name="invline_net_equation",
            ),
            models.CheckConstraint(condition=Q(net_amount__gte=0), name="invline_net_gte_0"),
        ]

    def __str__(self):
        return f"{self.description} x {self.quantity}"
