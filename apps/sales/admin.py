from django.contrib import admin

from .models import Invoice


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "customer_name", "date", "total", "status", "payment_status")
    search_fields = ("number", "customer_name", "customer_mobile")

    def has_delete_permission(self, request, obj=None):
        return False  # historical financial records are never deleted
