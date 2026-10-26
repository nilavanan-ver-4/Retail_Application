import django_filters as df
from django.db.models import F, Q

from .models import Brand, Category, Product


class ProductFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search (name, SKU, barcode)")
    category = df.ModelChoiceFilter(queryset=Category.objects.all())
    brand = df.ModelChoiceFilter(queryset=Brand.objects.all())
    status = df.ChoiceFilter(method="by_status", label="Status", empty_label="Active only",
                             choices=[("archived", "Archived"), ("all", "All")])
    stock = df.ChoiceFilter(method="by_stock", label="Stock", empty_label="Any",
                            choices=[("low", "Low stock"), ("out", "Out of stock"), ("in", "In stock")])
    order = df.OrderingFilter(fields=(("name", "name"), ("sku", "sku"), ("selling_price", "price"),
                                      ("stock_on_hand", "stock")), label="Sort by")

    class Meta:
        model = Product
        fields = ["q", "category", "brand", "status", "stock"]

    def search(self, qs, name, value):
        return qs.filter(Q(name__icontains=value) | Q(sku__icontains=value) | Q(barcode__icontains=value))

    def by_status(self, qs, name, value):
        return qs.filter(is_active=False) if value == "archived" else qs

    def by_stock(self, qs, name, value):
        if value == "low":
            return qs.filter(stock_on_hand__lte=F("reorder_level"))
        if value == "out":
            return qs.filter(stock_on_hand__lte=0)
        return qs.filter(stock_on_hand__gt=0)

    @property
    def qs(self):
        qs = super().qs
        if self.data.get("status") not in ("archived", "all"):
            qs = qs.filter(is_active=True)
        return qs
