from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from apps.core.models import TimeStampedModel


class PurchaseOrder(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ORDERED = "ordered", "Ordered"
        PARTIAL = "partial", "Partially received"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT,
                                 related_name="purchase_orders")
    order_date = models.DateField()
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                   validators=[MinValueValidator(Decimal("0"))])
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "purchasing_purchaseorder"
        ordering = ["-order_date", "-id"]
        indexes = [models.Index(fields=["status", "order_date"])]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("purchasing:order_detail", args=[self.pk])

    def __str__(self):
        return self.number

    @property
    def subtotal(self):
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def tax_total(self):
        return sum((line.tax_amount for line in self.lines.all()), Decimal("0.00"))

    @property
    def total(self):
        return self.subtotal + self.tax_total - self.discount


class PurchaseOrderLine(models.Model):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    received_quantity = models.PositiveIntegerField(default=0, editable=False)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0"))])
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                   validators=[MinValueValidator(Decimal("0"))])
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        db_table = "purchasing_purchaseorderline"
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=1), name="pol_quantity_gte_1"),
            models.CheckConstraint(condition=models.Q(received_quantity__lte=models.F("quantity")),
                                   name="pol_received_lte_ordered"),
        ]

    @property
    def outstanding(self):
        return self.quantity - self.received_quantity

    @property
    def line_total(self):
        return (self.unit_cost * self.quantity - self.discount).quantize(Decimal("0.01"))

    @property
    def tax_amount(self):
        return (self.line_total * self.tax_rate / 100).quantize(Decimal("0.01"))


class GoodsReceipt(TimeStampedModel):
    """Physical receipt of goods. Stock increases ONLY when a receipt is
    confirmed, and a receipt can be confirmed exactly once."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"

    number = models.CharField(max_length=30, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT,
                                 related_name="receipts")
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="receipts")
    receipt_date = models.DateField()
    delivery_note = models.CharField("Supplier delivery note / reference", max_length=100, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey("accounts.User", null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="+")
    comments = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                   related_name="+")
    legacy_item_id = models.IntegerField(null=True, blank=True, unique=True, editable=False)

    class Meta:
        db_table = "purchasing_goodsreceipt"
        ordering = ["-receipt_date", "-id"]
        permissions = [("receive_goods", "Can confirm goods receipts (increases stock)")]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("purchasing:receipt_detail", args=[self.pk])

    def __str__(self):
        return self.number


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    po_line = models.ForeignKey(PurchaseOrderLine, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="receipt_lines")
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2,
                                    validators=[MinValueValidator(Decimal("0"))])

    class Meta:
        db_table = "purchasing_goodsreceiptline"
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gte=1),
                                              name="grl_quantity_gte_1")]


class SupplierInvoice(TimeStampedModel):
    """The supplier's bill. Recording it is separate from paying it."""

    class Status(models.TextChoices):
        PENDING = "Pending", "Pending"
        CONFIRMED = "Confirmed", "Confirmed"
        CANCELLED = "Cancelled", "Cancelled"

    class PaymentStatus(models.TextChoices):
        UNPAID = "Unpaid", "Unpaid"
        PARTIAL = "Partially Paid", "Partially paid"
        PAID = "Paid", "Paid"

    invoice_number = models.CharField(max_length=50, unique=True)
    supplier = models.ForeignKey("suppliers.Supplier", on_delete=models.PROTECT,
                                 related_name="invoices")
    purchase_order = models.ForeignKey(PurchaseOrder, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="invoices")
    receipt = models.ForeignKey(GoodsReceipt, null=True, blank=True, on_delete=models.PROTECT,
                                related_name="invoices")
    invoice_date = models.DateField()
    due_date = models.DateField(null=True, blank=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2,
                                       validators=[MinValueValidator(Decimal("0"))])
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"),
                                      editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices,
                                      default=PaymentStatus.UNPAID)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "purchasing_supplierinvoice"
        ordering = ["-invoice_date", "-id"]
        indexes = [models.Index(fields=["payment_status"]), models.Index(fields=["supplier"])]
        constraints = [
            models.CheckConstraint(condition=models.Q(paid_amount__gte=0), name="sinv_paid_gte_0"),
            models.CheckConstraint(condition=models.Q(paid_amount__lte=models.F("total_amount")),
                                   name="sinv_paid_lte_total"),
        ]

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("purchasing:invoice_detail", args=[self.pk])

    def __str__(self):
        return self.invoice_number

    @property
    def balance(self):
        return self.total_amount - self.paid_amount


class SupplierInvoiceLine(models.Model):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("products.Product", on_delete=models.PROTECT, related_name="+")
    quantity = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        db_table = "purchasing_supplierinvoiceline"


class SupplierPayment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        BANK = "bank_transfer", "Bank transfer"
        UPI = "upi", "UPI"
        CARD = "credit_card", "Card"
        OTHER = "other", "Other"

    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2,
                                 validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=20, choices=Method.choices)
    reference = models.CharField(max_length=100, blank=True)
    paid_on = models.DateField()
    idempotency_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    created_by = models.ForeignKey("accounts.User", null=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        db_table = "purchasing_supplierpayment"
        ordering = ["-paid_on", "-id"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0),
                                              name="spay_amount_gt_0")]
