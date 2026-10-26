from decimal import Decimal

from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .validators import validate_image_upload


class Category(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "products_category"
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_category_name_ci")]

    def __str__(self):
        return self.name


class Brand(models.Model):
    name = models.CharField(max_length=100)

    class Meta:
        db_table = "products_brand"
        ordering = ["name"]
        constraints = [models.UniqueConstraint(Lower("name"), name="uniq_brand_name_ci")]

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    """A sellable item.

    ``id`` keeps the legacy ``add_product.pro_id`` value. ``stock_on_hand`` is a
    denormalised running balance that is only ever changed by
    ``apps.inventory.services.apply_movement`` (under a row lock), which also
    writes the matching ledger row; a CHECK constraint forbids negative stock
    unless ``allow_negative_stock`` is set.
    """

    sku = models.CharField(max_length=64, help_text="Unique stock keeping unit.")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.PROTECT, related_name="products")
    barcode = models.CharField(max_length=64, blank=True, null=True)
    image = models.ImageField(
        upload_to="products/%Y/%m/",
        blank=True,
        validators=[validate_image_upload, FileExtensionValidator(["jpg", "jpeg", "png", "webp"])],
    )
    unit = models.CharField(max_length=50, default="pcs")
    hs_code = models.CharField("HS code", max_length=50, blank=True)
    supplier = models.ForeignKey(
        "suppliers.Supplier", null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    supplier_terms = models.TextField(blank=True)

    cost_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=Decimal("0.00"), validators=[MinValueValidator(Decimal("0"))]
    )
    selling_price = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percent, from a verified tax rule. 0 means untaxed.",
    )

    stock_on_hand = models.IntegerField(default=0, editable=False)
    reorder_level = models.PositiveIntegerField(
        default=0, help_text="Legacy 'safety level'. Low-stock alerts trigger at or below this."
    )
    allow_negative_stock = models.BooleanField(default=False)
    expiry_date = models.DateField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        db_table = "products_product"
        ordering = ["name"]
        permissions = [
            ("view_cost_price", "Can view product cost prices and margins"),
            ("import_products", "Can import products from CSV/Excel"),
            ("export_products", "Can export products to CSV"),
        ]
        constraints = [
            models.UniqueConstraint(Lower("sku"), name="uniq_product_sku_ci"),
            models.UniqueConstraint(
                fields=["barcode"],
                condition=models.Q(barcode__isnull=False) & ~models.Q(barcode=""),
                name="uniq_product_barcode",
            ),
            models.CheckConstraint(condition=models.Q(cost_price__gte=0), name="product_cost_gte_0"),
            models.CheckConstraint(condition=models.Q(selling_price__gte=0), name="product_price_gte_0"),
            models.CheckConstraint(
                condition=models.Q(tax_rate__gte=0) & models.Q(tax_rate__lte=100), name="product_tax_0_100"
            ),
            models.CheckConstraint(
                condition=models.Q(stock_on_hand__gte=0) | models.Q(allow_negative_stock=True),
                name="product_stock_non_negative",
            ),
        ]
        indexes = [
            models.Index(fields=["name"]),
            models.Index(fields=["is_active", "category"]),
            models.Index(fields=["stock_on_hand"]),
        ]

    def __str__(self):
        return f"{self.sku} - {self.name}"

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("products:detail", args=[self.pk])

    def save(self, *args, **kwargs):
        self.sku = self.sku.strip()
        if self.barcode is not None:
            self.barcode = self.barcode.strip() or None
        super().save(*args, **kwargs)

    # -- derived values -------------------------------------------------------
    @property
    def unit_profit(self) -> Decimal:
        return self.selling_price - self.cost_price

    @property
    def profit_percentage(self):
        """Legacy rule: (selling - cost) / cost * 100; undefined when cost is 0."""
        if not self.cost_price:
            return None
        return ((self.selling_price - self.cost_price) / self.cost_price * 100).quantize(Decimal("0.01"))

    @property
    def stock_value(self) -> Decimal:
        return self.cost_price * max(self.stock_on_hand, 0)

    @property
    def availability_level(self) -> int:
        """Legacy 1-5 availability scale (replicates DB trigger
        ``update_inventory_availability_scale``): ratio of stock to safety level."""
        # safety level 0 -> ratio forced to 1.0 (legacy avoids division by zero)
        ratio = (
            Decimal(self.stock_on_hand) / Decimal(self.reorder_level) if self.reorder_level else Decimal(1)
        )
        if ratio >= 1:
            return 5
        if ratio >= Decimal("0.85"):
            return 4
        if ratio >= Decimal("0.50"):
            return 3
        if ratio >= Decimal("0.25"):
            return 2
        return 1

    @property
    def is_low_stock(self) -> bool:
        return self.stock_on_hand <= self.reorder_level

    @property
    def is_out_of_stock(self) -> bool:
        return self.stock_on_hand <= 0

    def archive(self):
        self.is_active = False
        self.archived_at = timezone.now()
        self.save(update_fields=["is_active", "archived_at", "updated_at"])

    def restore(self):
        self.is_active = True
        self.archived_at = None
        self.save(update_fields=["is_active", "archived_at", "updated_at"])
