"""Every screen renders for a permitted user, and is protected for others."""

from decimal import Decimal as D

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.inventory.services import start_stocktake
from apps.purchasing import services as pur
from apps.returns.services import request_return
from apps.sales.services import create_invoice


@pytest.fixture
def world(manager, make_product, customer, supplier):
    apple = make_product("Apple", "70", "50", stock=100, tax="6")
    milk = make_product("Milk", "45", "30", stock=3, reorder=10)
    inv, _ = create_invoice(
        user=manager,
        customer=customer,
        discount_percent=D("5"),
        lines=[{"product_id": apple.pk, "quantity": 4}, {"product_id": milk.pk, "quantity": 1}],
        payments=[{"method": "cash", "amount": D("100")}],
    )
    pay = inv.payments.first()
    ret = request_return(
        invoice_id=inv.pk,
        reason="faulty",
        user=manager,
        lines=[{"invoice_line_id": inv.lines.first().pk, "quantity": 1, "disposition": "damaged"}],
    )
    po = pur.create_purchase_order(
        supplier=supplier,
        order_date=timezone.localdate(),
        expected_date=None,
        user=manager,
        lines=[{"product": apple, "quantity": 10, "unit_cost": D("50"), "tax_rate": D("0")}],
    )
    grn = pur.create_receipt(
        supplier=supplier,
        receipt_date=timezone.localdate(),
        user=manager,
        purchase_order=po,
        lines=[{"product": apple, "quantity": 5, "unit_cost": D("52"), "po_line": po.lines.first()}],
    )
    pur.confirm_receipt(grn.pk, manager)
    sinv = pur.record_supplier_invoice(
        supplier=supplier,
        invoice_number=None,
        invoice_date=timezone.localdate(),
        due_date=None,
        user=manager,
        receipt=grn,
        lines=[{"product": apple, "quantity": 5, "unit_cost": D("52")}],
    )
    st = start_stocktake("Q4 count", manager)
    return dict(
        apple=apple,
        milk=milk,
        inv=inv,
        pay=pay,
        ret=ret,
        po=po,
        grn=grn,
        sinv=sinv,
        st=st,
        customer=customer,
        supplier=supplier,
    )


def urls(w):
    return [
        reverse("core:dashboard"),
        reverse("products:list"),
        reverse("products:create"),
        reverse("products:detail", args=[w["apple"].pk]),
        reverse("products:update", args=[w["apple"].pk]),
        reverse("products:categories"),
        reverse("products:import"),
        reverse("products:export"),
        reverse("inventory:stock"),
        reverse("inventory:movements"),
        reverse("inventory:adjust"),
        reverse("inventory:stocktakes"),
        reverse("inventory:stocktake", args=[w["st"].pk]),
        reverse("inventory:reconcile"),
        reverse("customers:list"),
        reverse("customers:create"),
        reverse("customers:detail", args=[w["customer"].pk]),
        reverse("customers:update", args=[w["customer"].pk]),
        reverse("customers:ledger"),
        reverse("customers:ledger_add"),
        reverse("customers:import"),
        reverse("customers:export"),
        reverse("suppliers:list"),
        reverse("suppliers:create"),
        reverse("suppliers:detail", args=[w["supplier"].pk]),
        reverse("suppliers:update", args=[w["supplier"].pk]),
        reverse("suppliers:import"),
        reverse("purchasing:order_list"),
        reverse("purchasing:order_create"),
        reverse("purchasing:order_detail", args=[w["po"].pk]),
        reverse("purchasing:receipt_list"),
        reverse("purchasing:receipt_create"),
        reverse("purchasing:receipt_create") + f"?po={w['po'].pk}",
        reverse("purchasing:receipt_detail", args=[w["grn"].pk]),
        reverse("purchasing:quick_receive", args=[w["apple"].pk]),
        reverse("purchasing:invoice_list"),
        reverse("purchasing:invoice_create"),
        reverse("purchasing:invoice_detail", args=[w["sinv"].pk]),
        reverse("sales:list"),
        reverse("sales:create"),
        reverse("sales:create") + f"?from={w['inv'].pk}",
        reverse("sales:detail", args=[w["inv"].pk]),
        reverse("sales:print", args=[w["inv"].pk]),
        reverse("sales:product_search") + "?q=app",
        reverse("sales:customer_lookup") + "?mobile=0123456789",
        reverse("payments:list"),
        reverse("payments:export"),
        reverse("returns:list"),
        reverse("returns:create") + f"?invoice={w['inv'].pk}",
        reverse("returns:detail", args=[w["ret"].pk]),
        reverse("reports:index"),
        reverse("audit:list"),
        reverse("accounts:user_list"),
        reverse("accounts:user_create"),
        reverse("accounts:password_change"),
    ] + [
        reverse("reports:report", args=[k])
        for k in (
            "sales-summary",
            "sales-by-product",
            "top-products",
            "sales-by-category",
            "sales-by-customer",
            "sales-by-supplier",
            "discounts",
            "tax-summary",
            "profit-estimate",
            "payment-methods",
            "daily-collections",
            "receivables",
            "payables",
            "returns",
            "reconciliation",
            "inventory-valuation",
            "low-stock",
            "stock-ageing",
            "purchases",
        )
    ]


def test_every_page_renders_for_admin(admin_user, client_as, world):
    c = client_as(admin_user)
    failures = []
    for u in urls(world):
        r = c.get(u)
        if r.status_code != 200:
            failures.append((u, r.status_code))
    assert not failures, failures


def test_pdf_and_exports(admin_user, client_as, world):
    c = client_as(admin_user)
    r = c.get(reverse("sales:pdf", args=[world["inv"].pk]))
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and r.content.startswith(b"%PDF")
    for key in ("sales-summary", "inventory-valuation", "receivables"):
        for fmt, ctype in (("csv", "text/csv"), ("xlsx", "spreadsheetml"), ("pdf", "application/pdf")):
            r = c.get(reverse("reports:report", args=[key]) + f"?format={fmt}")
            assert r.status_code == 200 and ctype in r["Content-Type"], (key, fmt, r.status_code)


def test_anonymous_is_redirected_to_login(client, world):
    for u in urls(world):
        r = client.get(u)
        assert r.status_code == 302 and "/accounts/login/" in r["Location"], u


def test_reports_require_dates_valid(admin_user, client_as):
    c = client_as(admin_user)
    r = c.get(reverse("reports:report", args=["sales-summary"]) + "?date_from=2025-02-01&date_to=2025-01-01")
    assert r.status_code == 200 and b"must not be after" in r.content
    assert c.get(reverse("reports:report", args=["nope"])).status_code == 404


def test_invoice_print_has_branding_and_words(admin_user, client_as, world):
    html = client_as(admin_user).get(reverse("sales:print", args=[world["inv"].pk])).content.decode()
    assert "ASKER ENTERPRISE" in html and "Ringgit" in html and "only" in html
