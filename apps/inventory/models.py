from django.db import models

from apps.core.models import TimeStampedModel


class StockMovement(models.Model):
    """Immutable stock ledger. ``quantity`` is signed (+in / -out) and
    ``balance_after`` records the product's stock right after the movement, so
    the ledger can always be reconciled against ``Product.stock_on_hand``."""

    class Type(models.TextChoices):
        OPENING = "opening", "Opening balance"
        PURCHASE = "purchase", "Purchase receipt"
        SALE = "sale", "Sale"
        SALE_CANCEL = "sale_cancel", "Sale cancelled"
        SALE_RETURN = "sale_return", "Customer return (resellable)"
        RETURN_DAMAGED = "return_damaged", "Customer return (damaged write-off)"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        DAMAGE = "damage", "Damaged / expired write-off"
        COUNT = "count", "Stock-take correction"
        IMPORT = "import", "Import"

    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=Type.choices)
    quantity = models.IntegerField()
    balance_after = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    reference_type = models.CharField(max_length=40, blank=True)
    reference_id = models.CharField(max_length=40, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(db_index=True)

    class Meta:
        db_table = "inventory_stockmovement"
        ordering = ["-created_at", "-id"]
        permissions = [("adjust_stock", "Can make manual stock adjustments and write-offs")]
        indexes = [
            models.Index(fields=["product", "created_at"]),
            models.Index(fields=["reference_type", "reference_id"]),
        ]
        constraints = [
            models.CheckConstraint(condition=~models.Q(quantity=0), name="movement_quantity_nonzero")
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Stock movements are immutable; post a correcting movement.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Stock movements cannot be deleted.")


class StockTake(TimeStampedModel):
    """A physical count session. Applying it posts COUNT movements for every
    line whose counted quantity differs from the system quantity."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPLIED = "applied", "Applied"

    name = models.CharField(max_length=120)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL, related_name="+")
    applied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "inventory_stocktake"
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class StockTakeLine(models.Model):
    stocktake = models.ForeignKey(StockTake, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    system_quantity = models.IntegerField()
    counted_quantity = models.IntegerField(null=True, blank=True)

    class Meta:
        db_table = "inventory_stocktakeline"
        constraints = [
            models.UniqueConstraint(fields=["stocktake", "product"], name="uniq_stocktake_product"),
            models.CheckConstraint(
                condition=models.Q(counted_quantity__isnull=True) | models.Q(counted_quantity__gte=0),
                name="stocktake_counted_gte_0",
            ),
        ]

    @property
    def variance(self):
        return None if self.counted_quantity is None else self.counted_quantity - self.system_quantity
