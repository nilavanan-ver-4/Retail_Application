from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.products.models import Product
from apps.sales.models import Invoice
from apps.sales.services import create_invoice

from .permissions import ModelPermissionsWithView
from .serializers import (CreateInvoiceSerializer, CustomerSerializer, InvoiceSerializer, ProductSerializer,
                          StockMovementSerializer)


class ProductViewSet(viewsets.ReadOnlyModelViewSet):
    """Catalogue and live stock levels (read-only)."""
    serializer_class = ProductSerializer
    permission_classes = [ModelPermissionsWithView]
    queryset = Product.objects.filter(is_active=True).select_related("category", "brand").order_by("name")
    filterset_fields = ["category", "brand", "is_active"]
    search_fields = ["name", "sku", "barcode"]
    ordering_fields = ["name", "selling_price", "stock_on_hand"]


class CustomerViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      viewsets.GenericViewSet):
    serializer_class = CustomerSerializer
    permission_classes = [ModelPermissionsWithView]
    queryset = Customer.objects.filter(is_active=True).order_by("name")
    search_fields = ["name", "mobile", "email"]


class InvoiceViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                     viewsets.GenericViewSet):
    """Create and read invoices. Creation runs through the same service as the web UI."""
    permission_classes = [ModelPermissionsWithView]
    queryset = Invoice.objects.prefetch_related("lines", "payments").order_by("-date", "-id")
    filterset_fields = ["status", "payment_status", "customer"]
    search_fields = ["number", "customer_name", "customer_mobile"]
    ordering_fields = ["date", "total"]

    def get_serializer_class(self):
        return CreateInvoiceSerializer if self.action == "create" else InvoiceSerializer

    def create(self, request, *args, **kwargs):
        ser = CreateInvoiceSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        user = request.user
        invoice, created = create_invoice(
            user=user, customer_mobile=d["customer_mobile"], customer_name=d["customer_name"],
            lines=[dict(ln) for ln in d["lines"]], discount_percent=d["discount_percent"],
            shipping_charge=d["shipping_charge"], payments=[dict(p) for p in d["payments"]], terms=d["terms"],
            submission_token=d["submission_token"], can_override_price=user.has_perm("sales.override_price"),
            can_override_credit=user.has_perm("customers.override_credit_limit"))
        return Response(InvoiceSerializer(invoice).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class StockMovementViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = StockMovementSerializer
    permission_classes = [ModelPermissionsWithView]
    queryset = StockMovement.objects.select_related("product").order_by("-created_at", "-id")
    filterset_fields = ["product", "movement_type"]
