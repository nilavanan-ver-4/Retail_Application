"""Guards against N+1 queries: page query counts must not grow with the number of rows shown."""

from decimal import Decimal as D

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.customers.models import Customer
from apps.payments.services import record_payment
from apps.sales.services import create_invoice

PAGES = [
    ("products:list", []),
    ("inventory:stock", []),
    ("inventory:movements", []),
    ("customers:list", []),
    ("sales:list", []),
    ("payments:list", []),
    ("suppliers:list", []),
    ("purchasing:invoice_list", []),
    ("returns:list", []),
    ("customers:ledger", []),
    ("core:dashboard", []),
    ("audit:list", []),
    ("reports:report", ["sales-summary"]),
    ("reports:report", ["sales-by-product"]),
    ("reports:report", ["receivables"]),
    ("reports:report", ["inventory-valuation"]),
    ("reports:report", ["low-stock"]),
    ("reports:report", ["stock-ageing"]),
]


def grow(manager, make_product, n):
    for i in range(n):
        p = make_product(f"Item {i}", stock=500)
        c = Customer.objects.create(name=f"Customer {i}", mobile=f"0{1000 + len(Customer.objects.all()) + i}")
        inv, _ = create_invoice(user=manager, customer=c, lines=[{"product_id": p.pk, "quantity": 2}])
        record_payment(inv.pk, amount=D("50"), method="cash", user=manager)


def count_queries(client, name, args):
    with CaptureQueriesContext(connection) as ctx:
        r = client.get(reverse(name, args=args))
    assert r.status_code == 200, (name, r.status_code)
    return len(ctx)


def test_query_counts_do_not_grow_with_row_count(admin_user, manager, make_product, client_as):
    c = client_as(admin_user)
    grow(manager, make_product, 3)
    small = {(n, tuple(a)): count_queries(c, n, a) for n, a in PAGES}
    grow(manager, make_product, 20)
    large = {(n, tuple(a)): count_queries(c, n, a) for n, a in PAGES}
    regress = {k: (small[k], large[k]) for k in small if large[k] > small[k] + 2}
    assert not regress, f"query count grows with data (N+1?): {regress}"
    assert max(large.values()) <= 40, large  # absolute ceiling per page


def test_invoice_detail_and_print_query_budget(admin_user, manager, make_product, customer, client_as):
    products = [make_product(f"P{i}", stock=50) for i in range(12)]
    inv, _ = create_invoice(
        user=manager, customer=customer, lines=[{"product_id": p.pk, "quantity": 1} for p in products]
    )
    c = client_as(admin_user)
    for name in ("sales:detail", "sales:print"):
        assert count_queries(c, name, [inv.pk]) <= 20, name  # 12 lines must not mean 12+ extra queries
