from decimal import Decimal as D

import pytest
from rest_framework.test import APIClient

from apps.customers.models import Customer
from apps.inventory.services import reconcile
from apps.sales.models import Invoice

pytestmark = pytest.mark.django_db


def api_as(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def test_anonymous_rejected():
    for url in ("/api/v1/products/", "/api/v1/customers/", "/api/v1/invoices/", "/api/v1/stock-movements/"):
        assert APIClient().get(url).status_code in (401, 403)


def test_token_auth_flow(cashier):
    c = APIClient()
    r = c.post("/api/v1/auth/token/", {"username": "cashier1", "password": "S3cure-test-pw!"}, format="json")
    assert r.status_code == 200
    c.credentials(HTTP_AUTHORIZATION="Token " + r.json()["token"])
    assert c.get("/api/v1/products/").status_code == 200
    assert (
        APIClient()
        .post("/api/v1/auth/token/", {"username": "cashier1", "password": "bad"}, format="json")
        .status_code
        == 400
    )


def test_product_listing_hides_cost_without_permission(cashier, manager, product):
    assert "cost_price" not in api_as(cashier).get("/api/v1/products/").json()["results"][0]
    assert api_as(manager).get("/api/v1/products/").json()["results"][0]["cost_price"] == "50.00"


def test_pagination_search_and_filter(manager, make_product):
    for i in range(59):
        make_product(f"Item {i:02d}", stock=0)
    make_product("Unique-Zebra", stock=0)
    r = api_as(manager).get("/api/v1/products/").json()
    assert r["count"] == 60 and len(r["results"]) == 50 and r["next"]
    assert api_as(manager).get("/api/v1/products/?search=Zebra").json()["count"] == 1


def test_view_permission_required_for_reads(inventory_staff, cashier):
    assert api_as(inventory_staff).get("/api/v1/customers/").status_code == 403
    assert api_as(cashier).get("/api/v1/stock-movements/").status_code == 403
    assert api_as(cashier).get("/api/v1/customers/").status_code == 200


def test_customer_create_ignores_privileged_fields(cashier):
    r = api_as(cashier).post(
        "/api/v1/customers/",
        {"name": "N", "mobile": "0123456", "credit_limit": "99999", "is_active": False},
        format="json",
    )
    assert r.status_code == 201
    c = Customer.objects.get(mobile="0123456")
    assert c.credit_limit is None and c.is_active  # mass-assignment blocked


def test_create_invoice_via_api_recalculates_everything(cashier, product):
    payload = {
        "customer_mobile": "0188",
        "customer_name": "API Cust",
        "submission_token": "api-1",
        "lines": [{"product_id": product.pk, "quantity": 2}],
        "total": "1.00",
        "final_amount": "1.00",
        "payments": [{"method": "cash", "amount": "140.00"}],
    }
    r = api_as(cashier).post("/api/v1/invoices/", payload, format="json")
    assert (
        r.status_code == 201 and D(r.json()["total"]) == D("140.00") and r.json()["payment_status"] == "Paid"
    )
    again = api_as(cashier).post("/api/v1/invoices/", payload, format="json")
    assert again.status_code == 200 and Invoice.objects.count() == 1  # retry-safe
    assert reconcile() == []


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"lines": [{"product_id": 1, "quantity": 0}]}, 400),
        ({"lines": []}, 400),
        ({"discount_percent": "150"}, 400),
        ({"lines": [{"product_id": 1, "quantity": 1, "unit_price": "0.01"}]}, 400),
        ({"lines": [{"product_id": 1, "quantity": 9999}]}, 400),
    ],
)
def test_api_invalid_invoice_rejected(cashier, product, patch, code):
    base = {
        "customer_mobile": "0177",
        "customer_name": "X",
        "lines": [{"product_id": product.pk, "quantity": 1}],
    }
    for ln in patch.get("lines", []):
        if ln.get("product_id") == 1:
            ln["product_id"] = product.pk
    r = api_as(cashier).post("/api/v1/invoices/", {**base, **patch}, format="json")
    assert r.status_code == code and Invoice.objects.count() == 0


def test_api_cannot_modify_or_delete(manager, product, customer):
    c = api_as(manager)
    inv, _ = __import__("apps.sales.services", fromlist=["x"]).create_invoice(
        user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 1}]
    )
    for method, url, body in (
        ("delete", f"/api/v1/invoices/{inv.pk}/", None),
        ("patch", f"/api/v1/invoices/{inv.pk}/", {"total": "1"}),
        ("put", f"/api/v1/invoices/{inv.pk}/", {"total": "1"}),
        ("delete", f"/api/v1/products/{product.pk}/", None),
    ):
        r = getattr(c, method)(url, body, format="json") if body else getattr(c, method)(url)
        assert r.status_code in (403, 405), (method, url, r.status_code)
    inv.refresh_from_db()
    assert inv.total == D("70.00")


def test_schema_and_docs_require_staff(admin_user, cashier):
    assert APIClient().get("/api/schema/").status_code == 302
    c = APIClient()
    c.force_login(cashier)
    assert c.get("/api/schema/").status_code == 302
    admin_user.is_staff = True
    admin_user.save()
    c = APIClient()
    c.force_login(admin_user)
    r = c.get("/api/schema/")
    assert r.status_code == 200 and b"openapi" in r.content
