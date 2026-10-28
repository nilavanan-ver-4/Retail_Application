"""Load test (Locust). Run only against a STAGING/synthetic database - it creates invoices.

    SEED: python manage.py seed_demo --password '...' --products 2000 --customers 500 --invoices 2000
    RUN : locust -f tests/perf/locustfile.py --headless -u 25 -r 5 -t 90s --host http://127.0.0.1:8765 \
              --csv /tmp/locust --html /tmp/locust.html
    ENV : PERF_PASSWORD (password given to seed_demo)
"""

import os
import random
import re

from locust import HttpUser, between, task

PASSWORD = os.environ.get("PERF_PASSWORD", "Demo-Passw0rd-2026!")
TOKEN_RE = re.compile(r'name="token" value="([0-9a-f]+)"')


class Staff(HttpUser):
    wait_time = between(0.5, 2)
    username = "demo_manager"

    def on_start(self):
        r = self.client.get("/accounts/login/", name="GET login")
        csrf = self.client.cookies.get("csrftoken")
        r = self.client.post(
            "/accounts/login/",
            {"username": self.username, "password": PASSWORD, "csrfmiddlewaretoken": csrf},
            name="POST login",
        )
        if r.status_code != 200 or "login" in r.url:
            raise RuntimeError("login failed")
        self.products = [f"Demo product {i:04d}" for i in range(1, 400)]

    def _csrf(self):
        return {"X-CSRFToken": self.client.cookies.get("csrftoken", "")}

    @task(10)
    def product_search(self):
        q = random.choice(self.products)[:-1]
        self.client.get(f"/sales/lookup/products/?q={q}", name="product search (JSON)")

    @task(4)
    def product_list(self):
        self.client.get(f"/products/?q=Demo&page={random.randint(1, 20)}", name="product list")

    @task(3)
    def inventory(self):
        self.client.get("/inventory/?level=low", name="inventory low-stock")

    @task(6)
    def create_sale(self):
        page = self.client.get("/sales/new/", name="GET billing screen")
        m = TOKEN_RE.search(page.text)
        if not m:
            return
        ids = [self._pid(random.choice(self.products)) for _ in range(random.randint(1, 3))]
        ids = [i for i in ids if i]
        if not ids:
            return
        data = {
            "csrfmiddlewaretoken": self.client.cookies.get("csrftoken"),
            "token": m.group(1),
            "mobile": f"0{random.randint(1100000001, 1100000500)}",
            "name": "Load Customer",
            "product_id": ids,
            "quantity": [1] * len(ids),
            "discount": ["0"] * len(ids),
            "discount_percent": "0",
            "shipping_charge": "0",
            "pay_method": "cash",
            "pay_amount": "1",
            "pay_reference": "",
        }
        with self.client.post(
            "/sales/new/", data, name="POST create invoice", catch_response=True, allow_redirects=False
        ) as r:
            if r.status_code == 302:
                r.success()
            elif b"Not enough stock" in r.content or b"exceed" in r.content:
                r.success()  # business rule rejections are correct behaviour, not failures
            else:
                r.failure(f"unexpected {r.status_code}")

    def _pid(self, name):
        r = self.client.get(f"/sales/lookup/products/?q={name}", name="resolve product (JSON)")
        try:
            return r.json()["products"][0]["id"]
        except (ValueError, KeyError, IndexError):
            return None

    @task(3)
    def invoice_list_and_detail(self):
        r = self.client.get("/sales/?page=1", name="invoice list")
        m = re.search(r'href="/sales/(\d+)/"', r.text)
        if m:
            self.client.get(f"/sales/{m.group(1)}/", name="invoice detail")

    @task(2)
    def invoice_pdf(self):
        r = self.client.get("/sales/?page=1", name="invoice list (for pdf)")
        m = re.search(r'href="/sales/(\d+)/"', r.text)
        if m:
            self.client.get(f"/sales/{m.group(1)}/pdf/", name="invoice PDF generation")

    @task(4)
    def dashboard(self):
        self.client.get("/", name="dashboard")

    @task(2)
    def reports(self):
        self.client.get(
            "/reports/sales-summary/?date_from=2026-08-01&date_to=2026-10-31&period=week",
            name="report: sales summary",
        )
        self.client.get("/reports/inventory-valuation/", name="report: inventory valuation")
        self.client.get(
            "/reports/sales-by-product/?date_from=2026-08-01&date_to=2026-10-31",
            name="report: sales by product",
        )
        self.client.get("/reports/receivables/", name="report: receivables")

    @task(1)
    def customers(self):
        self.client.get("/customers/?q=Demo", name="customer list")
