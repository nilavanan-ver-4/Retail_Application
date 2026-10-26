from django import forms

from apps.products.models import Product

from .models import StockMovement


class AdjustmentForm(forms.Form):
    KINDS = [(StockMovement.Type.ADJUSTMENT, "Adjustment (increase or decrease)"),
             (StockMovement.Type.DAMAGE, "Damaged / expired write-off (decrease)")]
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True))
    kind = forms.ChoiceField(choices=KINDS)
    quantity = forms.IntegerField(help_text="Positive adds stock, negative removes. Write-offs are always removed.")
    reason = forms.CharField(max_length=255, help_text="Required - recorded in the audit log.")

    def clean(self):
        data = super().clean()
        q, kind = data.get("quantity"), data.get("kind")
        if q == 0:
            self.add_error("quantity", "Quantity cannot be zero.")
        if q and kind == StockMovement.Type.DAMAGE:
            data["quantity"] = -abs(q)
        return data


class StockTakeForm(forms.Form):
    name = forms.CharField(max_length=120, initial="Stock take")
