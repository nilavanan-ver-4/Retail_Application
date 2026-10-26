from django.contrib import admin

from .models import Customer, LedgerEntry

admin.site.register(Customer)
admin.site.register(LedgerEntry)
