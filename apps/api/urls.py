from django.contrib.admin.views.decorators import staff_member_required
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework.routers import DefaultRouter

from . import views

app_name = "api"
router = DefaultRouter()
router.register("products", views.ProductViewSet, basename="product")
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("invoices", views.InvoiceViewSet, basename="invoice")
router.register("stock-movements", views.StockMovementViewSet, basename="stockmovement")

urlpatterns = [
    path("v1/", include(router.urls)),
    path("v1/auth/token/", obtain_auth_token, name="token"),
    path("schema/", staff_member_required(SpectacularAPIView.as_view()), name="schema"),
    path("docs/", staff_member_required(SpectacularSwaggerView.as_view(url_name="api:schema")), name="docs"),
]
