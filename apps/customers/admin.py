from django.contrib import admin

from apps.core.admin import HistoricalDocumentAdmin

from .models import Customer, LedgerEntry

admin.site.register(Customer)
admin.site.register(LedgerEntry, HistoricalDocumentAdmin)
