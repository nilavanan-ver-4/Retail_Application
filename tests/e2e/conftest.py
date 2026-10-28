import os

import pytest

# Playwright's sync API runs an event loop in this thread; the tests also use the Django ORM directly to seed and
# assert on data, which Django blocks in async contexts. Standard, test-only workaround.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

pytest.importorskip("playwright")
from playwright.sync_api import sync_playwright  # noqa: E402

CHROMIUM = os.environ.get("PLAYWRIGHT_CHROMIUM") or (
    "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as pw:
        kw = {"args": ["--no-sandbox"]}
        if CHROMIUM:
            kw["executable_path"] = CHROMIUM
        b = pw.chromium.launch(**kw)
        yield b
        b.close()


@pytest.fixture
def page(browser, live_server):
    ctx = browser.new_context(base_url=live_server.url, viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.set_default_timeout(8000)
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.js_errors = errors
    yield pg
    ctx.close()


def login(page, username, password="S3cure-test-pw!"):
    page.goto("/accounts/login/")
    page.fill("#id_username", username)
    page.fill("#id_password", password)
    page.click("button[type=submit]")
    page.wait_for_url("**/")
