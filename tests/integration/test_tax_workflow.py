from decimal import Decimal as D

import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.products.forms import ProductForm
from apps.products.importers import ProductImporter
from apps.products.models import TaxConfiguration
from apps.sales.services import cancel_invoice, create_invoice


def settings_data(**overrides):
    return {
        "action": "settings",
        "label": "Demo tax",
        "registration_number": "DEMO-REG",
        "default_rate": "6.00",
        **overrides,
    }


def test_tax_settings_and_default_apply_to_new_products_only(client_as, manager, product):
    response = client_as(manager).post(reverse("taxes:index"), settings_data())
    assert response.status_code == 302
    assert TaxConfiguration.current().default_rate == D("6")
    assert ProductForm(user=manager).fields["tax_rate"].initial == D("6")
    product.refresh_from_db()
    assert product.tax_rate == D("0")
    assert AuditLog.objects.filter(action="tax.settings_updated", user=manager).exists()


@pytest.mark.parametrize("rate", ["-1", "100.01", "NaN", "Infinity", "6.123", "not-a-rate"])
def test_invalid_tax_settings_are_rejected_without_writes(client_as, manager, rate):
    response = client_as(manager).post(reverse("taxes:index"), settings_data(default_rate=rate))
    assert response.status_code == 400
    assert not TaxConfiguration.objects.exists()


def test_cashier_can_review_tax_but_cannot_change_it(client_as, cashier, product):
    client = client_as(cashier)
    assert client.get(reverse("taxes:index")).status_code == 200
    assert client.post(reverse("taxes:index"), settings_data()).status_code == 403
    assert (
        client.post(
            reverse("taxes:index"),
            {"action": "products", "products": [product.pk], "rate": "10", "reason": "tampering"},
        ).status_code
        == 403
    )
    assert client.get(reverse("taxes:summary")).status_code == 403


def test_rate_assignment_is_audited_and_keeps_existing_invoice_snapshot(
    client_as, manager, product, customer
):
    TaxConfiguration.objects.create(
        pk=1, label="Original tax", registration_number="ORIGINAL", default_rate=D("0")
    )
    invoice, _ = create_invoice(
        user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 1}]
    )
    client = client_as(manager)
    response = client.post(
        reverse("taxes:index"),
        {"action": "products", "products": [product.pk], "rate": "6", "reason": "Configure sample tax"},
    )
    assert response.status_code == 302
    client.post(reverse("taxes:index"), settings_data())
    invoice.refresh_from_db()
    assert invoice.tax_total == D("0")
    assert invoice.tax_label == "Original tax"
    assert invoice.tax_registration_number == "ORIGINAL"
    new_invoice, _ = create_invoice(
        user=manager,
        customer=customer,
        lines=[{"product_id": product.pk, "quantity": 2}],
        discount_percent=D("10"),
    )
    assert new_invoice.tax_total == D("7.56")
    assert new_invoice.total == D("133.56")
    assert new_invoice.lines.get().tax_rate == D("6")
    assert new_invoice.tax_label == "Demo tax"
    assert AuditLog.objects.filter(
        action="product.tax_updated", target_id=str(product.pk), user=manager
    ).exists()
    html = client.get(reverse("sales:print", args=[new_invoice.pk])).content.decode()
    assert "DEMO-REG" in html and "6.00%" in html and "133.56" in html


def test_assignment_requires_selection_and_reason(client_as, manager, product):
    client = client_as(manager)
    assert (
        client.post(reverse("taxes:index"), {"action": "products", "rate": "6", "reason": "test"}).status_code
        == 400
    )
    assert (
        client.post(
            reverse("taxes:index"), {"action": "products", "products": [product.pk], "rate": "6"}
        ).status_code
        == 400
    )
    product.refresh_from_db()
    assert product.tax_rate == D("0")


def test_summary_uses_saved_rates_and_excludes_cancelled_invoices(client_as, manager, make_product, customer):
    product = make_product(tax="6")
    invoice, _ = create_invoice(
        user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 1}]
    )
    cancelled, _ = create_invoice(
        user=manager, customer=customer, lines=[{"product_id": product.pk, "quantity": 2}]
    )
    cancel_invoice(cancelled.pk, user=manager, reason="Test cancelled invoice")
    product.tax_rate = D("10")
    product.save(update_fields=["tax_rate"])
    client = client_as(manager)
    response = client.get(reverse("taxes:summary"))
    assert response.status_code == 200
    assert response.context["total_tax"] == invoice.tax_total == D("4.20")
    assert response.context["groups"][0]["tax_rate"] == D("6")
    csv = client.get(reverse("taxes:summary"), {"format": "csv"})
    assert csv.status_code == 200 and "4.20" in csv.content.decode()
    assert (
        client.get(reverse("taxes:summary"), {"start": "2026-12-31", "end": "2026-01-01"}).status_code == 400
    )
    assert client.get(reverse("taxes:summary"), {"start": "invalid", "format": "csv"}).status_code == 400


def test_import_default_and_blank_update_preserve_existing_tax(manager, product):
    TaxConfiguration.objects.create(pk=1, default_rate=D("6"))
    importer = ProductImporter()
    context = importer.build_context("create")
    data, errors, _ = importer.validate_row(
        {"sku": "NEW-TAX", "name": "New product", "selling_price": "100"}, "create", context
    )
    assert not errors and D(data["tax_rate"]) == D("6")
    data, errors, _ = importer.validate_row(
        {"sku": product.sku, "name": product.name, "selling_price": "100"}, "update", context
    )
    assert not errors and D(data["tax_rate"]) == product.tax_rate
