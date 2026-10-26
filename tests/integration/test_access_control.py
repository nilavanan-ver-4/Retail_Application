"""Server-side authorization: roles can only do what the permission matrix allows."""

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.sales.services import create_invoice


@pytest.fixture
def inv(manager, product, customer):
    return create_invoice(user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 1}])[
        0
    ]


# (url name, args-factory, allowed roles) - GET access
MATRIX = [
    (
        "products:list",
        lambda w: [],
        {"cashier", "inventory_staff", "purchaser", "reporter", "manager", "admin_user"},
    ),
    ("products:create", lambda w: [], {"inventory_staff", "manager", "admin_user"}),
    ("products:import", lambda w: [], {"inventory_staff", "manager", "admin_user"}),
    ("inventory:adjust", lambda w: [], {"inventory_staff", "manager", "admin_user"}),
    (
        "inventory:movements",
        lambda w: [],
        {"inventory_staff", "purchaser", "reporter", "manager", "admin_user"},
    ),
    ("customers:list", lambda w: [], {"cashier", "reporter", "manager", "admin_user"}),
    ("customers:ledger", lambda w: [], {"manager", "admin_user"}),
    ("suppliers:list", lambda w: [], {"purchaser", "reporter", "manager", "admin_user"}),
    ("purchasing:order_create", lambda w: [], {"purchaser", "manager", "admin_user"}),
    ("purchasing:invoice_list", lambda w: [], {"purchaser", "reporter", "manager", "admin_user"}),
    ("sales:create", lambda w: [], {"cashier", "manager", "admin_user"}),
    ("sales:list", lambda w: [], {"cashier", "reporter", "manager", "admin_user"}),
    ("payments:list", lambda w: [], {"cashier", "reporter", "manager", "admin_user"}),
    ("returns:list", lambda w: [], {"cashier", "reporter", "manager", "admin_user"}),
    ("reports:report", lambda w: ["sales-summary"], {"reporter", "manager", "admin_user"}),
    ("reports:report", lambda w: ["receivables"], {"reporter", "manager", "admin_user"}),
    (
        "reports:report",
        lambda w: ["inventory-valuation"],
        {"inventory_staff", "purchaser", "reporter", "manager", "admin_user"},
    ),
    (
        "reports:report",
        lambda w: ["low-stock"],
        {"inventory_staff", "purchaser", "reporter", "manager", "admin_user"},
    ),
    ("audit:list", lambda w: [], {"manager", "admin_user"}),
    ("accounts:user_list", lambda w: [], {"admin_user"}),
    ("accounts:user_create", lambda w: [], {"admin_user"}),
]
ROLES = ["cashier", "inventory_staff", "purchaser", "reporter", "manager", "admin_user"]


@pytest.mark.parametrize("name,args,allowed", MATRIX, ids=[f"{m[0]}:{m[1](None)}" for m in MATRIX])
def test_role_matrix(name, args, allowed, request, client_as):
    url = reverse(name, args=args(None))
    for role in ROLES:
        user = request.getfixturevalue(role)
        client_as(user)  # logs in on the shared client
        status = request.getfixturevalue("client").get(url).status_code
        if role in allowed:
            assert status == 200, f"{role} should reach {url}, got {status}"
        else:
            assert status == 403, f"{role} must be refused {url}, got {status}"


def test_cashier_cannot_cancel_invoice_or_reverse_payment_or_approve(client_as, cashier, inv):
    c = client_as(cashier)
    assert c.post(reverse("sales:cancel", args=[inv.pk]), {"reason": "x"}).status_code == 403
    assert c.post(reverse("returns:decide", args=[1, "approve"])).status_code == 403
    assert c.post(reverse("payments:reverse", args=[1])).status_code == 403
    inv.refresh_from_db()
    assert inv.status != "Cancelled"


def test_cashier_cannot_see_cost_prices(client_as, cashier, product):
    html = client_as(cashier).get(reverse("products:detail", args=[product.pk])).content.decode()
    assert "Cost" not in html and "50.00" not in html
    assert "export" not in client_as(cashier).get(reverse("products:list")).content.decode().lower().replace(
        "export-", ""
    )


def test_cashier_cannot_change_product_price(client_as, cashier, product):
    r = client_as(cashier).post(
        reverse("products:update", args=[product.pk]), {"name": "x", "selling_price": "1"}
    )
    assert r.status_code == 403
    product.refresh_from_db()
    assert product.selling_price == 70


def test_inventory_staff_cannot_sell_or_see_customers(client_as, inventory_staff):
    c = client_as(inventory_staff)
    assert c.get(reverse("sales:create")).status_code == 403
    assert c.get(reverse("customers:list")).status_code == 403
    assert c.get(reverse("customers:lookup") + "?mobile=1").status_code == 403


def test_purchaser_cannot_adjust_stock_or_sell(client_as, purchaser, product):
    c = client_as(purchaser)
    assert (
        c.post(
            reverse("inventory:adjust"),
            {"product": product.pk, "kind": "adjustment", "quantity": 5, "reason": "x"},
        ).status_code
        == 403
    )
    assert c.get(reverse("sales:create")).status_code == 403


def test_reporter_is_read_only(client_as, reporter, product, customer, inv):
    c = client_as(reporter)
    for name, args in (
        ("products:create", []),
        ("customers:create", []),
        ("sales:create", []),
        ("purchasing:order_create", []),
        ("inventory:adjust", []),
    ):
        assert c.get(reverse(name, args=args)).status_code == 403, name
    assert c.post(reverse("sales:cancel", args=[inv.pk]), {"reason": "x"}).status_code == 403


def test_user_management_only_for_admin_and_cannot_self_escalate(client_as, manager, admin_user):
    assert client_as(manager).get(reverse("accounts:user_list")).status_code == 403
    c = client_as(admin_user)
    r = c.post(
        reverse("accounts:user_update", args=[admin_user.pk]),
        {"first_name": "A", "last_name": "B", "email": "a@b.co", "phone": "", "is_active": "", "roles": []},
    )
    assert r.status_code == 200  # form re-rendered with an error
    admin_user.refresh_from_db()
    assert admin_user.is_active and admin_user.groups.filter(name="Administrator").exists()


def test_role_change_is_audited(client_as, admin_user, cashier):
    from django.contrib.auth.models import Group

    mgr = Group.objects.get(name="Manager")
    client_as(admin_user).post(
        reverse("accounts:user_update", args=[cashier.pk]),
        {"first_name": "", "last_name": "", "email": "", "phone": "", "is_active": "on", "roles": [mgr.pk]},
    )
    assert cashier.groups.filter(name="Manager").exists()
    log = AuditLog.objects.get(action="user.roles_changed")
    assert (
        log.details["before"] == ["Cashier"]
        and log.details["after"] == ["Manager"]
        and log.username == admin_user.username
    )


def test_deactivated_user_cannot_log_in(client, cashier):
    cashier.is_active = False
    cashier.save()
    r = client.post(reverse("accounts:login"), {"username": "cashier1", "password": "S3cure-test-pw!"})
    assert r.status_code == 200 and b"Please enter a correct" in r.content
    assert client.get(reverse("sales:list")).status_code == 302


def test_logout_requires_post_and_ends_session(client_as, cashier):
    c = client_as(cashier)
    assert c.get(reverse("accounts:logout")).status_code == 405
    assert c.post(reverse("accounts:logout")).status_code == 302
    assert c.get(reverse("sales:list")).status_code == 302


def test_expired_session_requires_login(client_as, cashier, settings):
    from django.test import Client

    c = Client()
    c.force_login(cashier)
    s = c.session
    s.set_expiry(-10)
    s.save()
    assert c.get(reverse("sales:list")).status_code == 302


def test_login_lockout_after_repeated_failures(client, cashier, settings):
    for _ in range(5):
        client.post(reverse("accounts:login"), {"username": "cashier1", "password": "wrong"})
    r = client.post(reverse("accounts:login"), {"username": "cashier1", "password": "S3cure-test-pw!"})
    assert r.status_code in (403, 429) or b"locked" in r.content.lower() or b"too many" in r.content.lower()
    assert AuditLog.objects.filter(action="auth.login_failed").count() >= 5
