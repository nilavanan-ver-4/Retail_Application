from decimal import Decimal

from django.core.validators import MinValueValidator, RegexValidator
from django.db import models

from apps.core.models import TimeStampedModel

mobile_validator = RegexValidator(r"^\+?[0-9]{6,15}$",
                                  "Enter digits only (6-15), optionally starting with +.")


class Customer(TimeStampedModel):
    name = models.CharField(max_length=100)
    mobile = models.CharField(max_length=15, unique=True, validators=[mobile_validator])
    email = models.EmailField(max_length=100, blank=True)
    billing_address = models.TextField(blank=True)
    shipping_address = models.TextField(blank=True)
    credit_limit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Maximum outstanding balance. Leave empty for no limit.")
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "customers_customer"
        ordering = ["name"]
        permissions = [("override_credit_limit", "Can sell beyond a customer's credit limit")]
        indexes = [models.Index(fields=["name"])]
        constraints = [models.CheckConstraint(
            condition=models.Q(credit_limit__isnull=True) | models.Q(credit_limit__gte=0),
            name="customer_credit_limit_gte_0")]

    def __str__(self):
        return f"{self.name} ({self.mobile})"

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("customers:detail", args=[self.pk])


class LedgerEntry(models.Model):
    """Customer account statement. DEBIT increases what the customer owes
    (invoice, refund paid out); CREDIT decreases it (payment, return credit).
    ``balance`` is the running balance after the entry (debits - credits)."""

    class Type(models.TextChoices):
        DEBIT = "Debit", "Debit"
        CREDIT = "Credit", "Credit"

    class Source(models.TextChoices):
        SYSTEM = "system", "System"
        MANUAL = "manual", "Manual adjustment"
        LEGACY = "legacy", "Imported from legacy"

    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="ledger_entries")
    entry_date = models.DateTimeField()
    entry_type = models.CharField(max_length=6, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    description = models.TextField(blank=True)
    invoice = models.ForeignKey("sales.Invoice", null=True, blank=True, on_delete=models.PROTECT,
                                related_name="ledger_entries")
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.SYSTEM)
    created_by = models.ForeignKey("accounts.User", null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "customers_ledgerentry"
        ordering = ["-entry_date", "-id"]
        indexes = [models.Index(fields=["customer", "entry_date"])]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0),
                                              name="ledger_amount_gte_0")]
