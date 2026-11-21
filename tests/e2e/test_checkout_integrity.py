import pytest
from playwright.sync_api import expect

from apps.inventory.services import apply_movement
from apps.sales.models import Invoice
from tests.e2e.conftest import login

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


def test_scanner_enter_before_debounce_completes_sale(page, cashier, make_product):
    scanned = make_product(name="Z scanned product", barcode="SCAN123", stock=0)
    apply_movement(scanned.pk, "opening", 2)
    make_product(name="A SCAN123 name collision", stock=0)
    login(page, cashier.username)
    page.goto("/sales/new/")
    page.fill("#mobile", "088888888")
    page.fill("#name", "Scanner customer")
    page.fill("#product-search", "SCAN123")
    page.press("#product-search", "Enter")
    expect(page.locator("#lines tbody tr .nm")).to_have_text(scanned.name)
    page.click("#pay-full")
    page.click("#save-sale")
    page.wait_for_url("**/sales/*/")
    inv = Invoice.objects.get()
    assert inv.lines.get().product_id == scanned.pk
    assert inv.total_paid == inv.total == 70
    scanned.refresh_from_db()
    assert scanned.stock_on_hand == 1
    assert not page.js_errors


def test_stale_product_search_cannot_replace_new_results(page, cashier, make_product):
    old = make_product(name="Old product")
    new = make_product(name="New product")
    delayed = []

    def intercept(route):
        if "q=Old" in route.request.url:
            delayed.append(route)
        else:
            route.continue_()

    login(page, cashier.username)
    page.goto("/sales/new/")
    page.route("**/lookup/products/**", intercept)
    with page.expect_request("**/lookup/products/?q=Old"):
        page.fill("#product-search", "Old")
    page.fill("#product-search", "New")
    expect(page.locator("#search-results li").first).to_contain_text(new.name)
    assert delayed
    delayed[0].fulfill(
        json={
            "products": [
                {
                    "id": old.pk,
                    "name": old.name,
                    "sku": old.sku,
                    "price": "70.00",
                    "stock": 100,
                }
            ]
        }
    )
    page.wait_for_timeout(100)
    expect(page.locator("#search-results li").first).to_contain_text(new.name)
    page.press("#product-search", "Enter")
    expect(page.locator("#lines tbody tr .nm")).to_have_text(new.name)
    assert not page.js_errors
