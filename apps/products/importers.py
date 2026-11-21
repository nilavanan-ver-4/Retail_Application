from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date

from apps.audit.services import log as audit_log
from apps.core.imports import BaseImporter
from apps.core.money import q2
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement
from apps.suppliers.models import Supplier

from .models import Category, Product, TaxConfiguration


def _dec(value, field, errors, default=None, minimum=Decimal("0")):
    if value in ("", None):
        return default
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        errors.append(f"{field}: '{value}' is not a number.")
        return default
    if not d.is_finite() or d < minimum:
        errors.append(f"{field}: must be {minimum} or more.")
        return default
    return q2(d)


class ProductImporter(BaseImporter):
    kind = "products"
    required = ("sku", "name", "selling_price")
    aliases = {
        "pro_id": "sku",
        "pro_name": "name",
        "safety_level": "reorder_level",
        "hc_code": "hs_code",
        "terms_from_invoice": "supplier_terms",
        "mail": "email",
    }
    modes = (
        ("create", "Create new products only (existing SKUs are errors)"),
        ("update", "Create new and UPDATE existing (prices, details; quantity is ADDED as a receipt)"),
    )

    def build_context(self, mode):
        products = list(Product.objects.only("sku", "tax_rate"))
        return {
            "skus": {p.sku.lower(): p.pk for p in products},
            "tax_rates": {p.pk: p.tax_rate for p in products},
            "suppliers": set(Supplier.objects.values_list("pk", flat=True)),
            "default_tax_rate": TaxConfiguration.current().default_rate,
        }

    def identity(self, data):
        return data.get("sku", "").lower() or None

    def validate_row(self, row, mode, ctx):
        errors = []
        sku = (row.get("sku") or "").strip()
        name = (row.get("name") or "").strip()
        if not sku:
            errors.append("sku is required.")
        if not name:
            errors.append("name is required.")
        price = _dec(row.get("selling_price"), "selling_price", errors)
        if price is None and not errors:
            errors.append("selling_price is required.")
        cost = _dec(row.get("cost_price"), "cost_price", errors, Decimal("0.00"))
        tax_default = ctx.get("default_tax_rate", Decimal("0.00"))
        if mode == "update" and sku.lower() in ctx["skus"] and row.get("tax_rate") in ("", None):
            tax_default = ctx["tax_rates"][ctx["skus"][sku.lower()]]
        tax = _dec(row.get("tax_rate"), "tax_rate", errors, tax_default)
        if tax is not None and tax > 100:
            errors.append("tax_rate: must be at most 100.")
        qty_raw = (row.get("quantity") or "").strip()
        qty = 0
        if qty_raw:
            try:
                qty = int(Decimal(qty_raw))
                if qty < 0 or qty != Decimal(qty_raw):
                    raise ValueError
            except (ValueError, InvalidOperation):
                errors.append("quantity: must be a whole number of 0 or more.")
        reorder = 0
        if (row.get("reorder_level") or "").strip():
            try:
                reorder = int(Decimal(row["reorder_level"]))
                if reorder < 0:
                    raise ValueError
            except (ValueError, InvalidOperation):
                errors.append("reorder_level: must be a whole number of 0 or more.")
        expiry = None
        if (row.get("expiry_date") or "").strip():
            expiry = parse_date(row["expiry_date"].strip())
            if expiry is None:
                errors.append("expiry_date: use YYYY-MM-DD.")
        supplier_id = None
        if (row.get("supplier_id") or "").strip():
            try:
                supplier_id = int(Decimal(row["supplier_id"]))
            except (ValueError, InvalidOperation):
                errors.append("supplier_id: must be a number.")
            else:
                if supplier_id not in ctx["suppliers"]:
                    errors.append(f"supplier_id {supplier_id} does not exist.")
        exists = sku.lower() in ctx["skus"]
        if exists and mode != "update":
            errors.append(f"SKU '{sku}' already exists.")
        data = {
            "sku": sku,
            "name": name,
            "category": (row.get("category") or "General").strip() or "General",
            "unit": (row.get("unit") or "pcs").strip() or "pcs",
            "cost_price": str(cost),
            "selling_price": str(price) if price is not None else "",
            "tax_rate": str(tax),
            "reorder_level": reorder,
            "quantity": qty,
            "expiry_date": expiry.isoformat() if expiry else "",
            "supplier_id": supplier_id,
            "hs_code": (row.get("hs_code") or "").strip(),
            "barcode": (row.get("barcode") or "").strip(),
            "supplier_terms": (row.get("supplier_terms") or "").strip(),
        }
        return data, errors, "update" if exists else "create"

    def apply(self, rows, user):
        created = updated = 0
        for r in rows:
            d = r["data"]
            cat, _ = Category.objects.get_or_create(
                name__iexact=d["category"], defaults={"name": d["category"]}
            )
            fields = dict(
                name=d["name"],
                category=cat,
                unit=d["unit"],
                cost_price=Decimal(d["cost_price"]),
                selling_price=Decimal(d["selling_price"]),
                tax_rate=Decimal(d["tax_rate"]),
                reorder_level=d["reorder_level"],
                hs_code=d["hs_code"],
                supplier_id=d["supplier_id"],
                supplier_terms=d["supplier_terms"],
                barcode=d["barcode"] or None,
                expiry_date=d["expiry_date"] or None,
            )
            if r["action"] == "update":
                p = Product.objects.select_for_update().get(sku__iexact=d["sku"])
                old = (p.selling_price, p.cost_price, p.tax_rate)
                for k, v in fields.items():
                    setattr(p, k, v)
                p.save()
                if old != (p.selling_price, p.cost_price, p.tax_rate):
                    audit_log(
                        "product.price_changed",
                        p,
                        via="import",
                        old=[str(x) for x in old],
                        new=[str(p.selling_price), str(p.cost_price), str(p.tax_rate)],
                    )
                updated += 1
            else:
                p = Product.objects.create(sku=d["sku"], **fields)
                created += 1
            if d["quantity"]:
                apply_movement(
                    p.pk,
                    StockMovement.Type.IMPORT,
                    d["quantity"],
                    user=user,
                    note="Quantity from product import",
                    unit_cost=p.cost_price,
                )
        return {"created": created, "updated": updated}
