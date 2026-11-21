from django.contrib import admin

from apps.core.admin import HistoricalDocumentAdmin

from .models import SalesReturn

admin.site.register(SalesReturn, HistoricalDocumentAdmin)
