from django.urls import path

from . import views

app_name = "payments"
urlpatterns = [
    path("", views.PaymentListView.as_view(), name="list"),
    path("export/", views.PaymentExportView.as_view(), name="export"),
    path("invoice/<int:invoice_id>/add/", views.PaymentCreateView.as_view(), name="create"),
    path("<int:pk>/reverse/", views.PaymentReverseView.as_view(), name="reverse"),
]
