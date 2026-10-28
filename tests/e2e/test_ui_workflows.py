"""Browser end-to-end tests (real Chromium against a live server and PostgreSQL)."""

import re
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from apps.customers.models import Customer
from apps.payments.models import Payment
from apps.products.models import Category, Product
from apps.returns.models import SalesReturn
from apps.sales.models import Invoice
from apps.suppliers.models import Supplier
from tests.conftest import make_user
from tests.e2e.conftest import login

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def staff(db):
    from apps.accounts.roles import sync_roles

    sync_roles()
    return {
        "manager": make_user("e2e_manager", "Manager"),
        "cashier": make_user("e2e_cashier", "Cashier"),
        "inventory": make_user("e2e_inventory", "Inventory Staff"),
    }


def test_full_retail_workflow(page, staff):
    """login -> create product -> receive stock -> sell -> invoice -> pay -> return -> reports."""
    Category.objects.create(name="Fruits")
    Supplier.objects.create(name="ABC", company="ABC Pvt")

    # 1. inventory staff creates a product with opening stock
    login(page, "e2e_inventory")
    page.goto("/products/new/")
    page.fill("#id_sku", "APL-1")
    page.fill("#id_name", "Apple")
    page.select_option("#id_category", label="Fruits")
    page.select_option("#id_supplier", label="ABC (ABC Pvt)")
    page.fill("#id_unit", "kg")
    page.fill("#id_cost_price", "50")
    page.fill("#id_selling_price", "70")
    page.fill("#id_reorder_level", "5")
    page.fill("#id_opening_stock", "10")
    page.click("text=Save product")
    expect(page.locator("h1")).to_contain_text("Apple")
    expect(page.locator(".stat", has_text="On hand")).to_contain_text("10 kg")

    # 2. receive 20 more via the legacy 'add item' flow
    page.goto(f"/purchasing/receive/{Product.objects.get().pk}/")
    page.fill("#id_quantity", "20")
    page.click("text=Receive & record supplier invoice")
    expect(page.locator(".msg-success")).to_contain_text("Received 20")
    assert Product.objects.get().stock_on_hand == 30
    page.click("text=Sign out")

    # 3. cashier makes a sale with the keyboard-driven billing screen
    login(page, "e2e_cashier")
    page.goto("/sales/new/")
    page.fill("#mobile", "0123456789")
    page.fill("#name", "Walk-in Customer")
    page.evaluate("document.activeElement.blur()")  # '/' is only a shortcut outside text fields
    page.press("body", "/")  # shortcut focuses product search
    expect(page.locator("#product-search")).to_be_focused()
    page.keyboard.type("Appl")
    expect(page.locator("#search-results li").first).to_contain_text("Apple")
    page.keyboard.press("Enter")
    page.fill("#lines tbody tr .qty", "3")
    expect(page.locator("#t-total")).to_have_text("210.00")  # live preview
    page.click("#pay-full")
    expect(page.locator("#t-due")).to_have_text("0.00")
    page.click("#save-sale")
    expect(page.locator(".msg-success")).to_contain_text("created")
    inv = Invoice.objects.get()
    assert (
        inv.total == Decimal("210.00")
        and inv.payment_status == "Paid"
        and Product.objects.get().stock_on_hand == 27
    )
    expect(page.locator("h1")).to_contain_text(f"Invoice {inv.number}")
    expect(page.locator(".badge", has_text="Paid").first).to_be_visible()

    # 4. invoice PDF downloads (uses the browser session's cookies)
    pdf = page.request.get(f"/sales/{inv.pk}/pdf/")
    assert (
        pdf.status == 200
        and pdf.headers["content-type"] == "application/pdf"
        and pdf.body().startswith(b"%PDF")
    )

    # 5. cashier requests a return; manager approves it
    page.goto(f"/returns/new/?invoice={inv.pk}")
    page.fill("input[name^=qty_]", "1")
    page.fill("#reason", "Bruised fruit")
    page.click("text=Submit return")
    expect(page.locator(".msg-success")).to_contain_text("submitted for approval")
    expect(page.locator("text=Approve & process")).to_have_count(0)  # cashier cannot approve
    page.click("text=Sign out")

    login(page, "e2e_manager")
    ret = SalesReturn.objects.get()
    page.goto(f"/returns/{ret.pk}/")
    page.select_option("#id_refund_method", "cash")
    page.once("dialog", lambda d: d.accept())
    page.click("text=Approve & process")
    expect(page.locator(".msg-success")).to_contain_text("Return approved")
    assert Product.objects.get().stock_on_hand == 28
    assert Payment.objects.filter(kind="refund", amount=Decimal("70.00")).exists()

    # 6. reports reflect the new data
    page.goto(f"/reports/sales-summary/?date_from={inv.date}&date_to={inv.date}&period=day")
    expect(page.locator("tfoot")).to_contain_text("210.00")
    expect(page.locator("tfoot")).to_contain_text("70.00")  # credited
    page.goto("/")
    # dashboard chart is drawn from database figures: today's bar has height, earlier empty days do not
    heights = page.eval_on_selector_all(
        "svg.chart rect.bar", "els => els.map(e => parseFloat(e.getAttribute('height')))"
    )
    assert len(heights) == 14 and max(heights) > 0 and heights[-1] == max(heights)
    assert not page.js_errors, page.js_errors


def test_form_validation_errors_are_visible_and_accessible(page, staff):
    login(page, "e2e_inventory")
    page.goto("/products/new/")
    page.click("text=Save product")
    errs = page.locator(".field.has-error .error")
    assert errs.count() >= 3
    expect(page.locator("label[for=id_sku]")).to_be_visible()
    for el in page.locator("input:not([type=hidden]):not([type=checkbox]), select, textarea").all():
        assert el.get_attribute("id"), "every control needs an id so its <label> is associated"
        assert page.locator(f"label[for={el.get_attribute('id')}]").count() == 1


def test_billing_screen_rejects_oversell_with_message(page, staff):
    cat = Category.objects.create(name="X")
    Product.objects.create(
        sku="S1", name="Scarce", category=cat, selling_price=Decimal("5"), cost_price=Decimal("1")
    )
    from apps.inventory.services import apply_movement

    apply_movement(Product.objects.get().pk, "opening", 2)
    login(page, "e2e_cashier")
    page.goto("/sales/new/")
    page.fill("#mobile", "0111")
    page.fill("#name", "Buyer")
    page.fill("#product-search", "Scarce")
    page.wait_for_selector("#search-results li")
    page.keyboard.press("Enter")
    page.fill("#lines tbody tr .qty", "5")
    page.click("#save-sale")
    expect(page.locator(".msg-error")).to_contain_text("Not enough stock")
    assert Invoice.objects.count() == 0
    expect(page.locator("#lines tbody tr")).to_have_count(1)  # the cart survives the error


def test_double_click_on_save_creates_single_invoice(page, staff):
    cat = Category.objects.create(name="X")
    Product.objects.create(
        sku="S1", name="Item", category=cat, selling_price=Decimal("5"), cost_price=Decimal("1")
    )
    from apps.inventory.services import apply_movement

    apply_movement(Product.objects.get().pk, "opening", 50)
    login(page, "e2e_cashier")
    page.goto("/sales/new/")
    page.fill("#mobile", "0111")
    page.fill("#name", "Buyer")
    page.fill("#product-search", "Item")
    page.wait_for_selector("#search-results li")
    page.keyboard.press("Enter")
    page.dblclick("#save-sale")
    page.wait_for_url(re.compile(r"/sales/\d+/$"))
    assert Invoice.objects.count() == 1 and Product.objects.get().stock_on_hand == 49


def test_responsive_mobile_layout(browser, live_server, staff):
    ctx = browser.new_context(base_url=live_server.url, viewport={"width": 375, "height": 740})
    page = ctx.new_page()
    login(page, "e2e_manager")
    for path in ("/", "/products/", "/sales/", "/sales/new/", "/reports/"):
        page.goto(path)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1, f"horizontal scroll on mobile at {path}: {overflow}px"
    page.goto("/")
    sidebar = page.locator("nav.sidebar")
    assert sidebar.bounding_box()["x"] < 0  # hidden off-canvas on phones
    page.click("label.hamburger")
    page.wait_for_timeout(400)
    assert sidebar.bounding_box()["x"] >= 0  # opens via the hamburger
    ctx.close()


def test_tablet_and_desktop_layouts_show_sidebar(browser, live_server, staff):
    for width in (768, 1280):
        ctx = browser.new_context(base_url=live_server.url, viewport={"width": width, "height": 900})
        page = ctx.new_page()
        login(page, "e2e_manager")
        page.goto("/products/")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 1
        ctx.close()


def test_print_layout_hides_chrome_and_pdf_has_content(page, staff):
    manager = staff["manager"]
    cat = Category.objects.create(name="X")
    p = Product.objects.create(
        sku="S1", name="Item", category=cat, selling_price=Decimal("5"), cost_price=Decimal("1")
    )
    from apps.inventory.services import apply_movement
    from apps.sales.services import create_invoice

    apply_movement(p.pk, "opening", 10)
    inv, _ = create_invoice(
        user=manager,
        customer=Customer.objects.create(name="C", mobile="0123"),
        lines=[{"product_id": p.pk, "quantity": 2}],
    )
    login(page, "e2e_manager")
    page.goto(f"/sales/{inv.pk}/")
    page.emulate_media(media="print")
    expect(page.locator("nav.sidebar")).to_be_hidden()
    expect(page.locator("header.topbar")).to_be_hidden()
    page.goto(f"/sales/{inv.pk}/print/")
    expect(page.locator(".inv-head h1")).to_contain_text("ASKER ENTERPRISE")
    expect(page.locator(".grand td")).to_contain_text("10.00")
    pdf = page.pdf(format="A4", print_background=True)
    assert pdf.startswith(b"%PDF") and len(pdf) > 3000


def test_unauthorised_pages_show_403_page(page, staff):
    login(page, "e2e_cashier")
    resp = page.goto("/accounts/users/")
    assert resp.status == 403
    expect(page.locator("h1")).to_contain_text("403")
    expect(page.locator("text=Users")).to_have_count(0)  # nav item hidden for this role


def test_destructive_actions_ask_for_confirmation(page, staff):
    cat = Category.objects.create(name="X")
    p = Product.objects.create(
        sku="S1", name="Item", category=cat, selling_price=Decimal("5"), cost_price=Decimal("1")
    )
    login(page, "e2e_inventory")
    page.goto(f"/products/{p.pk}/")
    seen = []
    page.once("dialog", lambda d: (seen.append(d.message), d.dismiss()))
    page.click("text=Archive")
    assert seen and "Archive this product" in seen[0]
    p.refresh_from_db()
    assert p.is_active  # dismissed -> nothing happened
