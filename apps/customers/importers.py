from apps.core.imports import BaseImporter

from .forms import CustomerForm
from .models import Customer


class CustomerImporter(BaseImporter):
    kind = "customers"
    required = ("name", "mobile")
    aliases = {"mail": "email", "address": "billing_address"}

    def build_context(self, mode):
        return {"mobiles": set(Customer.objects.values_list("mobile", flat=True))}

    def identity(self, data):
        return data.get("mobile")

    def validate_row(self, row, mode, ctx):
        mobile = (row.get("mobile") or "").strip().replace(" ", "")
        if mobile.endswith(".0"):
            mobile = mobile[:-2]  # Excel stores numbers as floats
        data = {
            "name": (row.get("name") or "").strip(),
            "mobile": mobile,
            "email": (row.get("email") or "").strip(),
            "billing_address": (row.get("billing_address") or "").strip(),
        }
        form = CustomerForm(data={**data, "is_active": True})
        errors = []
        if not form.is_valid():
            errors = [
                f"{f}: {e}"
                for f, es in form.errors.items()
                for e in es
                if not (f == "mobile" and "already exists" in e)
            ]
        if mobile in ctx["mobiles"]:
            errors.append(f"Customer with mobile {mobile} already exists.")
        return data, errors, "create"

    def apply(self, rows, user):
        Customer.objects.bulk_create([Customer(**r["data"]) for r in rows])
        return {"created": len(rows)}
