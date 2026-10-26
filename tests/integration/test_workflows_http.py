"""End-to-end workflows through the real views/forms (Django test client)."""

import io
from decimal import Decimal as D

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.inventory.services import reconcile
from apps.payments.models import Payment
from apps.products.models import Product
from apps.purchasing.models import GoodsReceipt, PurchaseOrder, SupplierInvoice
from apps.returns.models import SalesReturn
from apps.sales.models import Invoice


def sale_post(product, qty=2, **extra):
    data = {
        "token": "t-" + str(product.pk) + str(qty),
        "mobile": "0199",
        "name": "Walk In",
        "date": "2025-03-01",
        "product_id": [product.pk],
        "quantity": [qty],
        "discount": ["0"],
        "discount_percent": "0",
        "shipping_charge": "0",
    }
    data.update(extra)
    return data


# ------------------------------------------------------------------ billing
def test_cashier_can_make_a_sale_with_split_payment(client_as, cashier, product):
    c = client_as(cashier)
    r = c.post(
        reverse("sales:create"),
        sale_post(
            product, 2, pay_method=["cash", "upi"], pay_amount=["100", "40"], pay_reference=["", "UPI-123"]
        ),
    )
    assert r.status_code == 302
    inv = Invoice.objects.get()
    assert inv.total == D("140.00") and inv.payment_status == "Paid" and inv.created_by == cashier
    assert inv.payments.count() == 2
    product.refresh_from_db()
    assert product.stock_on_hand == 98


def test_browser_tampering_cannot_change_price_or_totals(client_as, cashier, product):
    c = client_as(cashier)
    # forged fields: unit_price, total_amount, final_amount, discount (amount) - all ignored/refused
    r = c.post(
        reverse("sales:create"),
        sale_post(
            product, 1, unit_price=["1.00"], total_amount="1.00", final_amount="1.00", discount_amount="69.00"
        ),
    )
    assert r.status_code == 302
    inv = Invoice.objects.get()
    assert inv.total == D("70.00") and inv.lines.get().unit_price == D("70.00")  # catalogue price wins
    assert inv.payment_status == "Unpaid"


def test_price_override_field_is_honoured_for_managers_only(client_as, manager, product):
    r = client_as(manager).post(reverse("sales:create"), sale_post(product, 1, unit_price=["60.00"]))
    assert r.status_code == 302 and Invoice.objects.get().total == D("60.00")


def test_double_submit_creates_one_invoice(client_as, cashier, product):
    c = client_as(cashier)
    data = sale_post(product, 1)
    c.post(reverse("sales:create"), data)
    r = c.post(reverse("sales:create"), data)
    assert r.status_code == 302 and Invoice.objects.count() == 1
    product.refresh_from_db()
    assert product.stock_on_hand == 99


@pytest.mark.parametrize(
    "override,msg",
    [
        ({"quantity": ["0"]}, "positive whole number"),
        ({"quantity": ["-3"]}, "positive whole number"),
        ({"quantity": ["1.5"]}, "positive whole number"),
        ({"quantity": ["abc"]}, "not a valid number"),
        ({"discount": ["9999"]}, "exceeds the line amount"),
        ({"discount_percent": "101"}, "between 0 and 100"),
        ({"product_id": ["999999"]}, "no longer exist"),
        ({"product_id": ["x"]}, "Invalid product"),
        ({"quantity": ["101"]}, "Not enough stock"),
        (
            {"pay_method": ["cash"], "pay_amount": ["99999"], "pay_reference": [""]},
            "exceed the invoice total",
        ),
        ({"mobile": ""}, "mobile and name"),
        ({"date": "not-a-date"}, "check the date"),
    ],
)
def test_invalid_sale_is_rejected_with_message_and_nothing_saved(client_as, cashier, product, override, msg):
    r = client_as(cashier).post(reverse("sales:create"), sale_post(product, 1, **override))
    assert r.status_code == 200 and msg.lower() in r.content.decode().lower()
    assert Invoice.objects.count() == 0
    product.refresh_from_db()
    assert product.stock_on_hand == 100 and reconcile() == []


def test_cancelled_invoice_flow_via_http(client_as, manager, product, customer):
    c = client_as(manager)
    c.post(reverse("sales:create"), sale_post(product, 3))
    inv = Invoice.objects.get()
    assert c.post(reverse("sales:cancel", args=[inv.pk]), {"reason": ""}).status_code == 302
    inv.refresh_from_db()
    assert inv.status != "Cancelled"
    c.post(reverse("sales:cancel", args=[inv.pk]), {"reason": "duplicate order"})
    inv.refresh_from_db()
    product.refresh_from_db()
    assert inv.status == "Cancelled" and product.stock_on_hand == 100


def test_payment_form_is_idempotent(client_as, manager, product):
    c = client_as(manager)
    c.post(reverse("sales:create"), sale_post(product, 2))
    inv = Invoice.objects.get()
    data = {"amount": "50", "method": "cash", "paid_on": "2025-03-02", "token": "pay-1"}
    c.post(reverse("payments:create", args=[inv.pk]), data)
    c.post(reverse("payments:create", args=[inv.pk]), data)  # retried request
    assert Payment.objects.filter(invoice=inv).count() == 1
    c.post(reverse("payments:create", args=[inv.pk]), {**data, "amount": "999", "token": "pay-2"})
    assert Payment.objects.filter(invoice=inv).count() == 1  # overpayment refused


def test_return_workflow_via_http(client_as, cashier, manager, product):
    client_as(manager).post(
        reverse("sales:create"),
        sale_post(product, 2, pay_method=["cash"], pay_amount=["140"], pay_reference=[""]),
    )
    inv = Invoice.objects.get()
    line = inv.lines.get()
    client_as(cashier).post(
        reverse("returns:create"),
        {"invoice": inv.pk, f"qty_{line.pk}": "1", f"disp_{line.pk}": "resellable", "reason": "changed mind"},
    )
    ret = SalesReturn.objects.get()
    assert ret.status == "pending"
    assert client_as(cashier).post(reverse("returns:decide", args=[ret.pk, "approve"])).status_code == 403
    client_as(manager).post(reverse("returns:decide", args=[ret.pk, "approve"]), {"refund_method": "cash"})
    ret.refresh_from_db()
    inv.refresh_from_db()
    product.refresh_from_db()
    assert ret.status == "approved" and inv.returned_total == D("70.00") and product.stock_on_hand == 99
    assert Payment.objects.filter(kind="refund", amount=D("70.00")).exists()


# ------------------------------------------------------------------ products
def test_product_crud_archive_and_price_audit(client_as, inventory_staff, category, supplier):
    c = client_as(inventory_staff)
    r = c.post(
        reverse("products:create"),
        {
            "sku": "NEW1",
            "name": "Pear",
            "category": category.pk,
            "unit": "kg",
            "cost_price": "10",
            "selling_price": "15",
            "tax_rate": "0",
            "reorder_level": "5",
            "opening_stock": "20",
            "is_active": "on",
        },
    )
    assert r.status_code == 302
    p = Product.objects.get(sku="NEW1")
    assert p.stock_on_hand == 20 and StockMovement.objects.filter(product=p, movement_type="opening").exists()
    c.post(
        reverse("products:update", args=[p.pk]),
        {
            "sku": "NEW1",
            "name": "Pear",
            "category": category.pk,
            "unit": "kg",
            "cost_price": "10",
            "selling_price": "18",
            "tax_rate": "0",
            "reorder_level": "5",
            "is_active": "on",
        },
    )
    log = AuditLog.objects.get(action="product.price_changed")
    assert log.details["selling_price"] == ["15.00", "18.00"]
    c.post(reverse("products:archive", args=[p.pk]))
    p.refresh_from_db()
    assert not p.is_active and p.archived_at
    assert Product.objects.filter(pk=p.pk).exists()  # archived, never deleted


def test_duplicate_sku_and_negative_values_rejected(client_as, inventory_staff, product, category):
    base = {
        "sku": product.sku.lower(),
        "name": "Dup",
        "category": category.pk,
        "unit": "pcs",
        "cost_price": "1",
        "selling_price": "2",
        "tax_rate": "0",
        "reorder_level": "0",
        "is_active": "on",
    }
    r = client_as(inventory_staff).post(reverse("products:create"), base)
    assert r.status_code == 200 and b"already exists" in r.content
    for field, val in (("selling_price", "-1"), ("tax_rate", "150"), ("reorder_level", "-2")):
        r = client_as(inventory_staff).post(
            reverse("products:create"), {**base, "sku": "OK" + field, field: val}
        )
        assert r.status_code == 200 and not Product.objects.filter(sku="OK" + field).exists(), field


def test_selling_below_cost_requires_manager(client_as, inventory_staff, manager, category):
    base = {
        "sku": "LOW",
        "name": "Loss",
        "category": category.pk,
        "unit": "pcs",
        "cost_price": "10",
        "selling_price": "5",
        "tax_rate": "0",
        "reorder_level": "0",
        "is_active": "on",
    }
    assert client_as(inventory_staff).post(reverse("products:create"), base).status_code == 200
    assert client_as(manager).post(reverse("products:create"), base).status_code == 302


def test_bulk_update_atomic_and_audited(client_as, inventory_staff, make_product):
    a, b = make_product("A"), make_product("B")
    c = client_as(inventory_staff)
    c.post(reverse("products:bulk"), {"action": "price_percent", "value": "10", "selected": [a.pk, b.pk]})
    a.refresh_from_db()
    assert a.selling_price == D("77.00")
    c.post(reverse("products:bulk"), {"action": "tax_rate", "value": "500", "selected": [a.pk, b.pk]})
    a.refresh_from_db()
    assert a.tax_rate == 0  # whole batch rejected
    assert AuditLog.objects.filter(action="product.bulk_update").exists()


@pytest.mark.parametrize(
    "name,ctype,ok",
    [("p.png", "image/png", True), ("p.exe", "image/png", False), ("p.png", "image/png", False)],
)
def test_image_upload_validation(client_as, inventory_staff, category, name, ctype, ok, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    content = buf.getvalue() if ok or name.endswith(".exe") else b"<script>alert(1)</script>"
    r = client_as(inventory_staff).post(
        reverse("products:create"),
        {
            "sku": "IMG" + name,
            "name": "Img",
            "category": category.pk,
            "unit": "pcs",
            "cost_price": "1",
            "selling_price": "2",
            "tax_rate": "0",
            "reorder_level": "0",
            "is_active": "on",
            "image": SimpleUploadedFile(name, content, ctype),
        },
    )
    assert (r.status_code == 302) is ok


# ------------------------------------------------------------------ stock
def test_stock_adjustment_requires_reason_and_blocks_negative(client_as, inventory_staff, product):
    c = client_as(inventory_staff)
    r = c.post(
        reverse("inventory:adjust"),
        {"product": product.pk, "kind": "adjustment", "quantity": "-5", "reason": ""},
    )
    assert r.status_code == 200
    r = c.post(
        reverse("inventory:adjust"),
        {"product": product.pk, "kind": "adjustment", "quantity": "-500", "reason": "oops"},
    )
    assert r.status_code == 200 and b"Not enough stock" in r.content
    r = c.post(
        reverse("inventory:adjust"),
        {"product": product.pk, "kind": "damage", "quantity": "4", "reason": "dropped"},
    )
    assert r.status_code == 302
    product.refresh_from_db()
    assert product.stock_on_hand == 96  # damage always removes
    assert AuditLog.objects.filter(action="stock.adjusted").count() == 1


def test_stocktake_posts_corrections(client_as, inventory_staff, product):
    c = client_as(inventory_staff)
    c.post(reverse("inventory:stocktakes"), {"name": "Count"})
    from apps.inventory.models import StockTake

    st = StockTake.objects.get()
    line = st.lines.get()
    c.post(reverse("inventory:stocktake", args=[st.pk]), {f"count_{line.pk}": "90", "apply": "1"})
    product.refresh_from_db()
    st.refresh_from_db()
    assert product.stock_on_hand == 90 and st.status == "applied" and reconcile() == []
    c.post(reverse("inventory:stocktake", args=[st.pk]), {f"count_{line.pk}": "10", "apply": "1"})
    product.refresh_from_db()
    assert product.stock_on_hand == 90  # cannot be applied twice


# ------------------------------------------------------------------ purchasing
def test_purchase_flow_receipt_changes_stock_only_on_confirm(client_as, purchaser, product, supplier):
    c = client_as(purchaser)
    c.post(
        reverse("purchasing:order_create"),
        {
            "supplier": supplier.pk,
            "order_date": "2025-03-01",
            "place": "1",
            "form-TOTAL_FORMS": "2",
            "form-INITIAL_FORMS": "0",
            "form-0-product": product.pk,
            "form-0-quantity": "10",
            "form-0-unit_cost": "48",
            "form-1-product": "",
            "form-1-quantity": "",
        },
    )
    po = PurchaseOrder.objects.get()
    assert po.status == "ordered" and po.total == D("480.00")
    product.refresh_from_db()
    assert product.stock_on_hand == 100  # an order is not a receipt
    c.post(
        reverse("purchasing:receipt_create"),
        {
            "po": po.pk,
            "supplier": supplier.pk,
            "receipt_date": "2025-03-05",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "0",
            "form-0-product": product.pk,
            "form-0-quantity": "6",
            "form-0-unit_cost": "48",
        },
    )
    grn = GoodsReceipt.objects.get()
    product.refresh_from_db()
    assert grn.status == "draft" and product.stock_on_hand == 100
    c.post(reverse("purchasing:receipt_confirm", args=[grn.pk]))
    c.post(reverse("purchasing:receipt_confirm", args=[grn.pk]))  # duplicate confirm
    product.refresh_from_db()
    po.refresh_from_db()
    assert product.stock_on_hand == 106 and po.status == "partial"
    assert product.cost_price == D("48.00")  # latest purchase cost
    assert StockMovement.objects.filter(movement_type="purchase").count() == 1


def test_cannot_over_receive_a_po_line(client_as, purchaser, product, supplier, manager):
    from apps.purchasing import services as pur

    po = pur.create_purchase_order(
        supplier=supplier,
        order_date=timezone.localdate(),
        expected_date=None,
        user=manager,
        lines=[{"product": product, "quantity": 5, "unit_cost": D("50"), "tax_rate": D("0")}],
    )
    grn = pur.create_receipt(
        supplier=supplier,
        receipt_date=timezone.localdate(),
        user=manager,
        purchase_order=po,
        lines=[{"product": product, "quantity": 6, "unit_cost": D("50"), "po_line": po.lines.first()}],
    )
    r = client_as(purchaser).post(reverse("purchasing:receipt_confirm", args=[grn.pk]), follow=True)
    assert b"exceeds outstanding" in r.content
    product.refresh_from_db()
    assert product.stock_on_hand == 100


def test_supplier_invoice_and_payment_are_separate_steps(client_as, purchaser, product, supplier):
    c = client_as(purchaser)
    c.post(reverse("purchasing:quick_receive", args=[product.pk]), {"quantity": "10", "unit_cost": "50"})
    inv = SupplierInvoice.objects.get()
    assert inv.payment_status == "Unpaid" and inv.total_amount == D("500.00")
    assert len(inv.invoice_number) == 9 and inv.invoice_number.startswith(str(timezone.localdate().year))
    c.post(
        reverse("purchasing:invoice_pay", args=[inv.pk]),
        {"amount": "200", "method": "cash", "paid_on": "2025-03-05", "token": "sp-1"},
    )
    c.post(
        reverse("purchasing:invoice_pay", args=[inv.pk]),
        {"amount": "200", "method": "cash", "paid_on": "2025-03-05", "token": "sp-1"},
    )
    c.post(
        reverse("purchasing:invoice_pay", args=[inv.pk]),
        {"amount": "999", "method": "cash", "paid_on": "2025-03-05", "token": "sp-2"},
    )
    inv.refresh_from_db()
    assert inv.paid_amount == D("200.00") and inv.payment_status == "Partially Paid"


# ------------------------------------------------------------------ imports
def upload(c, name, content, url="products:import", mode="create", ctype="text/csv"):
    return c.post(reverse(url), {"file": SimpleUploadedFile(name, content, ctype), "mode": mode})


CSV = (
    b"sku,name,category,unit,cost_price,selling_price,quantity,reorder_level\n"
    b"S1,Rice,Grains,kg,40,55,10,5\nS2,Sugar,Grains,kg,35,50,0,3\n"
)


def test_product_import_preview_then_apply(client_as, inventory_staff):
    c = client_as(inventory_staff)
    r = upload(c, "p.csv", CSV)
    assert r.status_code == 302
    assert Product.objects.count() == 0  # nothing applied by uploading
    preview = c.get(r["Location"])
    assert b"Rice" in preview.content and b"create" in preview.content
    c.post(r["Location"])
    assert Product.objects.filter(sku__in=["S1", "S2"]).count() == 2
    assert Product.objects.get(sku="S1").stock_on_hand == 10 and reconcile() == []
    replay = c.post(r["Location"], follow=True)  # a second POST must not re-apply
    assert Product.objects.count() == 2 and b"already processed" in replay.content
    assert Product.objects.get(sku="S1").stock_on_hand == 10


def test_import_invalid_rows_block_unless_skipped(client_as, inventory_staff, product):
    bad = (
        b"sku,name,selling_price,cost_price,quantity\n"
        b"A1,Good,10,5,1\n,NoSku,10,5,1\nA2,Neg,-5,1,1\nA3,BadQty,5,1,x\n"
        + product.sku.encode()
        + b",DupExisting,5,1,1\nA1,DupInFile,5,1,1\n"
    )
    c = client_as(inventory_staff)
    r = upload(c, "bad.csv", bad)
    html = c.get(r["Location"]).content.decode()
    for needle in ("sku is required", "must be 0 or more", "quantity", "already exists", "Duplicate of row"):
        assert needle in html, needle
    c.post(r["Location"])  # blocked: errors, no skip flag
    assert not Product.objects.filter(sku="A1").exists()
    c.post(r["Location"], {"skip_invalid": "on"})
    assert Product.objects.filter(sku="A1").exists() and not Product.objects.filter(sku="A2").exists()


def test_import_update_mode_adds_quantity_and_audits_price(client_as, inventory_staff, product):
    c = client_as(inventory_staff)
    data = f"sku,name,selling_price,cost_price,quantity\n{product.sku},Apple,80,50,5\n".encode()
    r = upload(c, "u.csv", data, mode="update")
    c.post(r["Location"])
    product.refresh_from_db()
    assert product.selling_price == D("80.00") and product.stock_on_hand == 105
    assert AuditLog.objects.filter(action="product.price_changed").exists()


def test_import_legacy_headers_and_excel(client_as, inventory_staff):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "pro_id",
            "pro_name",
            "cost_price",
            "selling_price",
            "quantity",
            "unit",
            "category",
            "safety_level",
            "expiry_date",
            "supplier_id",
            "hc_code",
        ]
    )
    ws.append([101, "Legacy Apple", 50.0, 70.0, 100, "kg", "Fruits", 1, "2030-12-31", None, "HC1"])
    buf = io.BytesIO()
    wb.save(buf)
    c = client_as(inventory_staff)
    r = upload(c, "legacy.xlsx", buf.getvalue(), ctype="application/vnd.ms-excel")
    c.post(r["Location"])
    p = Product.objects.get(sku="101")
    assert (
        p.name == "Legacy Apple"
        and p.hs_code == "HC1"
        and p.stock_on_hand == 100
        and str(p.expiry_date) == "2030-12-31"
    )


@pytest.mark.parametrize(
    "name,content,msg",
    [
        ("x.txt", b"a,b", "Only .csv and .xlsx"),
        ("x.csv", b"", "empty"),
        ("x.csv", b"\xff\xfe\x00bad", "UTF-8"),
        ("x.xlsx", b"not a zip", "could not be read"),
        ("x.csv", b"sku,name\nA,B\n", "Missing required column"),
        ("../../etc/passwd.csv", b"sku\n", "Missing required"),
    ],
)
def test_import_rejects_bad_files(client_as, inventory_staff, name, content, msg):
    r = upload(client_as(inventory_staff), name, content)
    assert r.status_code == 200 and msg in r.content.decode()


def test_import_size_limit(client_as, inventory_staff, settings):
    settings.MAX_UPLOAD_MB = 0
    r = upload(client_as(inventory_staff), "big.csv", CSV)
    assert r.status_code == 200 and b"larger than" in r.content


def test_user_cannot_apply_someone_elses_import(client_as, inventory_staff, manager):
    r = upload(client_as(inventory_staff), "p.csv", CSV)
    assert client_as(manager).post(r["Location"]).status_code == 404


def test_customer_import_and_duplicate_mobile(client_as, manager, customer):
    data = b"name,mobile,address,mail\nA,0111111111,KL,a@a.com\nB,0123456789,x,b@b.com\nC,12,x,c\n"
    c = client_as(manager)
    r = upload(c, "c.csv", data, url="customers:import")
    html = c.get(r["Location"]).content.decode()
    assert "already exists" in html
    c.post(r["Location"], {"skip_invalid": "on"})
    assert Customer.objects.filter(mobile="0111111111").exists() and Customer.objects.count() == 2


def test_csv_export_neutralises_formula_injection(client_as, inventory_staff, make_product):
    make_product(name='=HYPERLINK("http://evil","x")')
    body = client_as(inventory_staff).get(reverse("products:export")).content.decode()
    assert "'=HYPERLINK" in body and ",=HYPERLINK" not in body


def test_exports_hide_cost_from_users_without_permission(client_as, cashier, manager, product):
    assert client_as(cashier).get(reverse("products:export")).status_code == 403
