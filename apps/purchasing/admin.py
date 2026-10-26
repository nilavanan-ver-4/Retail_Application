from django.contrib import admin

from .models import GoodsReceipt, PurchaseOrder, SupplierInvoice, SupplierPayment

for m in (PurchaseOrder, GoodsReceipt, SupplierInvoice, SupplierPayment):
    admin.site.register(m)
