"""Security regression tests (OWASP-oriented). All data is synthetic."""

import io

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from apps.products.models import Product
from apps.sales.services import create_invoice


def test_sql_injection_payloads_in_filters_are_inert(client_as, manager, product, customer):
    c = client_as(manager)
    payloads = [
        "' OR '1'='1",
        "'; DROP TABLE products_product; --",
        "1 UNION SELECT username,password FROM accounts_user--",
        "%' AND 1=1 --",
    ]
    for p in payloads:
        for url in (
            "products:list",
            "customers:list",
            "sales:list",
            "payments:list",
            "suppliers:list",
            "audit:list",
        ):
            r = c.get(reverse(url), {"q": p, "product": p, "customer": p, "status": p})
            assert r.status_code in (200,), (url, p, r.status_code)
        assert c.get(reverse("sales:product_search"), {"q": p}).status_code == 200
        assert c.get(reverse("customers:lookup"), {"mobile": p}).json() == {"exists": False}
    assert Product.objects.filter(pk=product.pk).exists()  # table still there, row intact
    body = c.get(reverse("products:list"), {"q": "' OR '1'='1"}).content.decode()
    assert product.name not in body  # the injection did not match everything


def test_xss_is_escaped_everywhere(client_as, manager, make_product, customer):
    evil = '<script>alert("xss")</script>'
    p = make_product(name=evil)
    customer.name = evil
    customer.save()
    inv, _ = create_invoice(
        user=manager, customer=customer, lines=[{"product_id": p.pk, "quantity": 1}], terms=evil
    )
    c = client_as(manager)
    for url in (
        reverse("products:list"),
        reverse("products:detail", args=[p.pk]),
        reverse("customers:list"),
        reverse("customers:detail", args=[customer.pk]),
        reverse("sales:list"),
        reverse("sales:detail", args=[inv.pk]),
        reverse("sales:print", args=[inv.pk]),
        reverse("inventory:stock"),
        reverse("sales:create") + f"?from={inv.pk}",
    ):
        html = c.get(url).content.decode()
        assert "<script>alert" not in html, url
    # JSON endpoints return data, never markup, and the billing page loads names via textContent
    assert c.get(reverse("sales:product_search"), {"q": "script"}).json()["products"][0]["name"] == evil
    js = open("static/js/invoice.js").read()
    assert (
        "innerHTML" in js
        and ".textContent = p.name" in js
        and "innerHTML = '<td>" not in js.split("tr.innerHTML")[0]
    )


def test_csrf_is_enforced_on_state_changing_posts(manager, product, customer):
    c = Client(enforce_csrf_checks=True)
    c.force_login(manager)
    for name, args in (
        ("sales:create", []),
        ("products:create", []),
        ("sales:cancel", [1]),
        ("inventory:adjust", []),
        ("accounts:logout", []),
        ("payments:create", [1]),
    ):
        assert c.post(reverse(name, args=args), {}).status_code == 403, name


def test_login_redirect_cannot_leave_the_site(client, cashier):
    r = client.post(
        reverse("accounts:login") + "?next=https://evil.example/phish",
        {"username": "cashier1", "password": "S3cure-test-pw!", "next": "https://evil.example/phish"},
    )
    assert r.status_code == 302 and "evil.example" not in r["Location"]


def test_security_headers_and_cookie_flags(client_as, cashier, settings):
    c = client_as(cashier)
    r = c.get(reverse("core:dashboard"))
    csp = r["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp and "object-src 'none'" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]  # no inline scripts
    assert r["X-Content-Type-Options"] == "nosniff" and r["X-Frame-Options"] == "DENY"
    assert r["Referrer-Policy"] == "same-origin" and "no-store" in r["Cache-Control"]
    r = Client().post(reverse("accounts:login"), {"username": "cashier1", "password": "S3cure-test-pw!"})
    cookie = r.cookies["sessionid"]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"


def test_templates_contain_no_inline_scripts():
    import pathlib
    import re

    for path in pathlib.Path("templates").rglob("*.html"):
        for m in re.finditer(r"<script\b([^>]*)>", path.read_text()):
            assert "src=" in m.group(1) or "json" in m.group(1), f"inline script in {path}"
        assert not re.search(r"\son\w+\s*=", path.read_text().replace("<!--", "")), (
            f"inline handler in {path}"
        )


def test_password_reset_does_not_enumerate_users(client, cashier):
    cashier.email = "cash@example.com"
    cashier.save()
    a = client.post(reverse("accounts:password_reset"), {"email": "cash@example.com"})
    b = client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
    assert a.status_code == b.status_code == 302 and a["Location"] == b["Location"]
    assert len(mail.outbox) == 1 and "password reset" in mail.outbox[0].subject.lower()


def test_password_reset_token_single_use_and_policy(client, cashier):
    cashier.email = "cash@example.com"
    cashier.save()
    client.post(reverse("accounts:password_reset"), {"email": "cash@example.com"})
    import re

    link = re.search(r"http\S+/password/reset/(\S+)/(\S+)/", mail.outbox[0].body)
    url = reverse("accounts:password_reset_confirm", args=[link.group(1), link.group(2)])
    page = client.get(url, follow=True)
    weak = client.post(page.request["PATH_INFO"], {"new_password1": "12345678", "new_password2": "12345678"})
    assert weak.status_code == 200 and b"too common" in weak.content or b"entirely numeric" in weak.content
    ok = client.post(
        page.request["PATH_INFO"],
        {"new_password1": "Brand-new-Passw0rd!", "new_password2": "Brand-new-Passw0rd!"},
    )
    assert ok.status_code == 302
    reuse = Client().get(url, follow=True)
    assert b"invalid or has expired" in reuse.content


def test_password_policy_enforced_on_change(client_as, cashier):
    r = client_as(cashier).post(
        reverse("accounts:password_change"),
        {"old_password": "S3cure-test-pw!", "new_password1": "short", "new_password2": "short"},
    )
    assert r.status_code == 200 and b"too short" in r.content


def test_uploaded_filename_cannot_escape_media_root(client_as, inventory_staff, category, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    buf = io.BytesIO()
    Image.new("RGB", (2, 2)).save(buf, "PNG")
    client_as(inventory_staff).post(
        reverse("products:create"),
        {
            "sku": "TRAV",
            "name": "Trav",
            "category": category.pk,
            "unit": "pcs",
            "cost_price": "1",
            "selling_price": "2",
            "tax_rate": "0",
            "reorder_level": "0",
            "is_active": "on",
            "image": SimpleUploadedFile("../../../etc/evil.png", buf.getvalue(), "image/png"),
        },
    )
    p = Product.objects.get(sku="TRAV")
    path = (tmp_path / p.image.name).resolve()
    assert str(path).startswith(str(tmp_path.resolve())) and ".." not in p.image.name


def test_oversize_and_polyglot_images_rejected(client_as, inventory_staff, category, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    big = io.BytesIO()
    Image.new("RGB", (2000, 2000), "white").save(big, "BMP")  # BMP is not an allowed format
    r = client_as(inventory_staff).post(
        reverse("products:create"),
        {
            "sku": "BMP",
            "name": "x",
            "category": category.pk,
            "unit": "pcs",
            "cost_price": "1",
            "selling_price": "2",
            "tax_rate": "0",
            "reorder_level": "0",
            "is_active": "on",
            "image": SimpleUploadedFile("a.png", big.getvalue(), "image/png"),
        },
    )
    assert r.status_code == 200 and not Product.objects.filter(sku="BMP").exists()


def test_pdf_renderer_refuses_external_and_file_urls():
    from apps.sales.pdf import safe_fetcher

    f = safe_fetcher()
    for url in ("file:///etc/passwd", "http://127.0.0.1:5432/", "https://example.com/x.png", "ftp://x/y"):
        with pytest.raises(Exception):  # noqa: B017 - any refusal is acceptable
            f.fetch(url)


def test_pdf_with_hostile_product_name_renders_safely(client_as, manager, make_product, customer):
    p = make_product(
        name='<img src="file:///etc/passwd"><link rel=stylesheet href="http://169.254.169.254/">'
    )
    inv, _ = create_invoice(user=manager, customer=customer, lines=[{"product_id": p.pk, "quantity": 1}])
    r = client_as(manager).get(reverse("sales:pdf", args=[inv.pk]))
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_error_pages_do_not_leak_internals(client_as, manager):
    r = client_as(manager).get("/definitely/not/here/")
    assert r.status_code == 404 and b"Traceback" not in r.content and b"settings" not in r.content.lower()


def test_audit_log_is_immutable_and_never_stores_passwords(client, cashier):
    from apps.audit.models import AuditLog

    client.post(reverse("accounts:login"), {"username": "cashier1", "password": "TopSecret-guess-1"})
    entry = AuditLog.objects.filter(action="auth.login_failed").first()
    assert entry and "TopSecret" not in str(entry.details) + entry.summary + entry.username
    with pytest.raises(PermissionError):
        entry.delete()
    with pytest.raises(PermissionError):
        AuditLog.objects.all().delete()
    entry.summary = "tamper"
    with pytest.raises(PermissionError):
        entry.save()


def test_django_deploy_checks_pass_with_production_settings(monkeypatch):
    """Run `manage.py check --deploy` in a clean subprocess with DEBUG off."""
    import os
    import secrets
    import subprocess
    import sys

    env = {
        **os.environ,
        "DEBUG": "false",
        "SECRET_KEY": secrets.token_urlsafe(64),
        "ALLOWED_HOSTS": "example.com",
        "SECURE_SSL_REDIRECT": "true",
    }
    out = subprocess.run(
        [sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stdout + out.stderr


def test_missing_secret_key_fails_closed_in_production():
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    env["DEBUG"] = "false"
    out = subprocess.run(
        [sys.executable, "manage.py", "check"], env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode != 0 and "SECRET_KEY" in out.stderr


def test_chart_labels_from_database_are_escaped(client_as, manager, make_product):
    """Regression: customer/product names reach SVG chart labels and must be escaped."""
    from apps.customers.models import Customer

    evil = Customer.objects.create(name="<img src=x onerror=alert(1)>", mobile="0555")
    p = make_product()
    create_invoice(user=manager, customer=evil, lines=[{"product_id": p.pk, "quantity": 1}])
    c = client_as(manager)
    for url in (
        reverse("core:dashboard"),
        reverse("reports:report", args=["sales-by-customer"]),
        reverse("reports:report", args=["receivables"]),
    ):
        html = c.get(url).content.decode()
        assert "<img src=x" not in html and "onerror=alert" not in html.replace(
            "&lt;img src=x onerror=alert(1)&gt;", ""
        ), url
