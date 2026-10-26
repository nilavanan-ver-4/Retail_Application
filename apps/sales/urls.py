from django.urls import path

from apps.customers.views import CustomerLookupView
from apps.products.views import ProductSearchView

from . import views

app_name = "sales"
urlpatterns = [
    path("", views.InvoiceListView.as_view(), name="list"),
    path("new/", views.InvoiceCreateView.as_view(), name="create"),
    path("<int:pk>/", views.InvoiceDetailView.as_view(), name="detail"),
    path("<int:pk>/print/", views.InvoicePrintView.as_view(), name="print"),
    path("<int:pk>/pdf/", views.InvoicePdfView.as_view(), name="pdf"),
    path("<int:pk>/cancel/", views.InvoiceCancelView.as_view(), name="cancel"),
    path("lookup/products/", ProductSearchView.as_view(), name="product_search"),
    path("lookup/customer/", CustomerLookupView.as_view(), name="customer_lookup"),
]
