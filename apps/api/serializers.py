from decimal import Decimal

from rest_framework import serializers

from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.payments.models import Payment
from apps.products.models import Product
from apps.sales.models import Invoice, InvoiceLine


class ProductSerializer(serializers.ModelSerializer):
    category = serializers.StringRelatedField()
    brand = serializers.StringRelatedField()

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "name",
            "category",
            "brand",
            "barcode",
            "unit",
            "selling_price",
            "tax_rate",
            "stock_on_hand",
            "reorder_level",
            "is_active",
            "expiry_date",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request and request.user.has_perm("products.view_cost_price"):
            data["cost_price"] = str(instance.cost_price)  # cost is never exposed without permission
        return data


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = [
            "id",
            "name",
            "mobile",
            "email",
            "billing_address",
            "shipping_address",
            "credit_limit",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at", "credit_limit", "is_active"]  # no mass assignment of limits


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = [
            "id",
            "product",
            "description",
            "unit",
            "quantity",
            "unit_price",
            "discount",
            "net_amount",
            "tax_rate",
            "tax_amount",
            "line_total",
        ]
        read_only_fields = fields


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "kind", "amount", "method", "reference", "receipt_no", "paid_on", "status"]
        read_only_fields = fields


class InvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)
    balance_due = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = [
            "id",
            "number",
            "customer",
            "customer_name",
            "customer_mobile",
            "date",
            "subtotal",
            "discount_percent",
            "discount_amount",
            "shipping_charge",
            "tax_total",
            "total",
            "total_paid",
            "returned_total",
            "balance_due",
            "status",
            "payment_status",
            "lines",
            "payments",
        ]
        read_only_fields = fields


class NewLineSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    quantity = serializers.IntegerField(min_value=1)
    discount = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"), required=False
    )


class NewPaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=Payment.Method.choices)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class CreateInvoiceSerializer(serializers.Serializer):
    """Totals are never accepted from the client - only product ids, quantities and discounts."""

    customer_mobile = serializers.CharField(max_length=15)
    customer_name = serializers.CharField(max_length=100)
    lines = NewLineSerializer(many=True, allow_empty=False)
    discount_percent = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
        required=False,
        default=Decimal("0"),
    )
    shipping_charge = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=Decimal("0"), required=False, default=Decimal("0")
    )
    payments = NewPaymentSerializer(many=True, required=False, default=list)
    terms = serializers.CharField(required=False, allow_blank=True, default="")
    submission_token = serializers.CharField(max_length=64, required=False, allow_null=True, default=None)


class StockMovementSerializer(serializers.ModelSerializer):
    product = serializers.StringRelatedField()

    class Meta:
        model = StockMovement
        fields = [
            "id",
            "product",
            "movement_type",
            "quantity",
            "balance_after",
            "reference_type",
            "reference_id",
            "note",
            "created_at",
        ]
        read_only_fields = fields
