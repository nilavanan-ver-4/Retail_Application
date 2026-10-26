from django.contrib import admin

from .models import Brand, Category, Product

admin.site.register(Category)
admin.site.register(Brand)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "selling_price", "stock_on_hand", "is_active")
    search_fields = ("sku", "name", "barcode")
    readonly_fields = ("stock_on_hand",)
