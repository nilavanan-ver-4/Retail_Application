from apps.core.imports import BaseImporter

from .models import Supplier


class SupplierImporter(BaseImporter):
    kind = "suppliers"
    required = ("name",)
    aliases = {"mail": "email"}

    def build_context(self, mode):
        return {"keys": {(s.name.lower(), s.company.lower()) for s in Supplier.objects.all()}}

    def identity(self, data):
        return (data.get("name", "").lower(), data.get("company", "").lower())

    def validate_row(self, row, mode, ctx):
        data = {k: (row.get(k) or "").strip() for k in ("name", "company", "mobile", "email", "terms")}
        errors = []
        if not data["name"]:
            errors.append("name is required.")
        if data["email"] and "@" not in data["email"]:
            errors.append("email is not valid.")
        if self.identity(data) in ctx["keys"]:
            errors.append("A supplier with this name and company already exists.")
        return data, errors, "create"

    def apply(self, rows, user):
        Supplier.objects.bulk_create([Supplier(**r["data"]) for r in rows])
        return {"created": len(rows)}
