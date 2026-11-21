import pytest
from playwright.sync_api import expect

from apps.products.models import TaxConfiguration
from apps.sales.models import Invoice
from tests.e2e.conftest import login

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_tax_settings_assignment_billing_and_invoice(page, manager, product):
    login(page, manager.username)
    page.get_by_role("link", name="Tax", exact=True).click()
    page.fill("#id_label", "Demo tax")
    page.fill("#id_registration_number", "DEMO-REG")
    page.fill("#id_default_rate", "6")
    page.get_by_role("button", name="Save tax settings").click()
    expect(page.locator(".msg-success")).to_contain_text("Tax settings saved")
    page.get_by_role("checkbox", name=f"Select {product.sku}", exact=True).check()
    page.fill("#id_rate", "6")
    page.fill("#id_reason", "Testing tax setup")
    page.get_by_role("button", name="Apply rate to selected products").click()
    expect(page.locator(".msg-success")).to_contain_text("Tax updated for 1 products")
    page.goto("/sales/new/")
    page.fill("#mobile", "0123456789")
    page.fill("#name", "Tax test customer")
    page.fill("#product-search", product.sku)
    page.locator("#search-results li").first.click()
    expect(page.locator("#lines .rate")).to_have_text("6.00%")
    expect(page.locator("#t-tax")).to_have_text("4.20")
    expect(page.locator("#t-total")).to_have_text("74.20")
    page.click("#pay-full")
    page.click("#save-sale")
    expect(page.get_by_role("heading", name="Invoice", exact=False).first).to_be_visible()
    invoice = Invoice.objects.get()
    assert str(invoice.tax_total) == "4.20"
    assert invoice.tax_label == TaxConfiguration.current().label == "Demo tax"
    expect(page.locator("main")).to_contain_text("6.00%")
    printed = page.request.get(f"/sales/{invoice.pk}/print/")
    assert "DEMO-REG" in printed.text() and "6.00%" in printed.text()
    pdf = page.request.get(f"/sales/{invoice.pk}/pdf/")
    assert pdf.ok and pdf.body().startswith(b"%PDF-")
