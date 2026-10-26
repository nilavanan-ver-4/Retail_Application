from django.urls import path

from . import views

app_name = "purchasing"
urlpatterns = [
    path("orders/", views.OrderListView.as_view(), name="order_list"),
    path("orders/new/", views.OrderCreateView.as_view(), name="order_create"),
    path("orders/<int:pk>/", views.OrderDetailView.as_view(), name="order_detail"),
    path("orders/<int:pk>/<str:action>/", views.OrderStatusView.as_view(), name="order_status"),
    path("receipts/", views.ReceiptListView.as_view(), name="receipt_list"),
    path("receipts/new/", views.ReceiptCreateView.as_view(), name="receipt_create"),
    path("receipts/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt_detail"),
    path("receipts/<int:pk>/confirm/", views.ReceiptConfirmView.as_view(), name="receipt_confirm"),
    path("receive/<int:product_id>/", views.QuickReceiveView.as_view(), name="quick_receive"),
    path("invoices/", views.SupplierInvoiceListView.as_view(), name="invoice_list"),
    path("invoices/new/", views.SupplierInvoiceCreateView.as_view(), name="invoice_create"),
    path("invoices/<int:pk>/", views.SupplierInvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/pay/", views.SupplierPaymentView.as_view(), name="invoice_pay"),
]
