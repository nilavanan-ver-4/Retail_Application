from django.contrib import admin

from apps.core.admin import HistoricalDocumentAdmin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(HistoricalDocumentAdmin):
    list_display = ("number", "customer_name", "date", "total", "status", "payment_status")
    search_fields = ("number", "customer_name", "customer_mobile")
