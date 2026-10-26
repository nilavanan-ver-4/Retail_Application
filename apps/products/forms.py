from decimal import Decimal

from django import forms

from .models import Brand, Category, Product


class ProductForm(forms.ModelForm):
    opening_stock = forms.IntegerField(
        min_value=0,
        required=False,
        initial=0,
        help_text="Units already on the shelf. Recorded as an opening balance.",
    )

    class Meta:
        model = Product
        fields = [
            "sku",
            "name",
            "category",
            "brand",
            "barcode",
            "description",
            "unit",
            "hs_code",
            "supplier",
            "supplier_terms",
            "cost_price",
            "selling_price",
            "tax_rate",
            "reorder_level",
            "allow_negative_stock",
            "expiry_date",
            "image",
            "is_active",
        ]
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 2}),
            "supplier_terms": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if self.instance.pk:
            del self.fields["opening_stock"]
        if user is not None and not user.has_perm("products.view_cost_price"):
            # staff without the permission can neither see nor change purchase costs
            del self.fields["cost_price"]
        elif "cost_price" in self.fields:
            self.fields["cost_price"].initial = Decimal("0.00")
        if user is not None and not user.has_perm("products.change_product"):
            self.fields["selling_price"].disabled = True

    def clean_sku(self):
        sku = self.cleaned_data["sku"].strip()
        qs = Product.objects.filter(sku__iexact=sku).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A product with this SKU already exists.")
        return sku

    def clean(self):
        data = super().clean()
        cost = data.get("cost_price") if "cost_price" in self.fields else self.instance.cost_price
        price = data.get("selling_price")
        can_override = bool(self.user and self.user.has_perm("sales.override_price"))
        if price is not None and cost is not None and price < cost and not can_override:
            self.add_error(
                "selling_price", "Selling price is below cost. Only a manager can set a price below cost."
            )
        return data


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name"]


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ["name"]


class BulkActionForm(forms.Form):
    ACTIONS = [
        ("archive", "Archive selected"),
        ("restore", "Restore selected"),
        ("category", "Set category"),
        ("tax_rate", "Set tax rate (%)"),
        ("reorder_level", "Set reorder level"),
        ("price_percent", "Adjust selling price by %"),
    ]
    action = forms.ChoiceField(choices=ACTIONS)
    value = forms.CharField(required=False)
