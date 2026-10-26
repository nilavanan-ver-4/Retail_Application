"""Every report figure is checked against totals worked out by hand (see comments)."""

from datetime import date
from decimal import Decimal as D

import pytest

from apps.reports import queries as q
from apps.returns.services import approve_return, request_return
from apps.sales.services import cancel_invoice, create_invoice

JAN1, FEB28 = date(2025, 1, 1), date(2025, 2, 28)


@pytest.fixture
def scenario(manager, make_product, customer):
    A = make_product("Alpha", "100", "60", stock=100, tax="10")
    B = make_product("Beta", "50", "20", stock=100, tax="0", reorder=100)
    # Inv1: A x2 (200) + B x1 (50) = 250, 10% off = 25 -> taxable A 180 / B 45 -> tax 18 -> total 243.00; cash 143 paid
    i1, _ = create_invoice(
        user=manager,
        customer=customer,
        invoice_date=date(2025, 1, 10),
        discount_percent=D("10"),
        lines=[{"product_id": A.pk, "quantity": 2}, {"product_id": B.pk, "quantity": 1}],
        payments=[{"method": "cash", "amount": D("143")}],
    )
    # Inv2: B x4 = 200 less line discount 20 = 180, no tax; paid fully by UPI
    i2, _ = create_invoice(
        user=manager,
        customer_mobile="0222",
        customer_name="Second",
        invoice_date=date(2025, 1, 10),
        lines=[{"product_id": B.pk, "quantity": 4, "discount": D("20")}],
        payments=[{"method": "upi", "amount": D("180")}],
    )
    # Inv3: A x1 = 100 + 10 tax = 110, unpaid
    i3, _ = create_invoice(
        user=manager,
        customer=customer,
        invoice_date=date(2025, 2, 5),
        lines=[{"product_id": A.pk, "quantity": 1}],
    )
    # Inv4 is cancelled and must appear nowhere
    i4, _ = create_invoice(
        user=manager,
        customer=customer,
        invoice_date=date(2025, 2, 6),
        lines=[{"product_id": B.pk, "quantity": 2}],
    )
    cancel_invoice(i4.pk, user=manager, reason="test")
    # return 1 x Alpha from Inv1: credit = (180 + 18) / 2 = 99.00
    ret = request_return(
        invoice_id=i1.pk,
        reason="r",
        user=manager,
        return_date=date(2025, 1, 20),
        lines=[{"invoice_line_id": i1.lines.get(product=A).pk, "quantity": 1, "disposition": "resellable"}],
    )
    approve_return(ret.pk, user=manager)
    return dict(A=A, B=B, i1=i1, i2=i2, i3=i3)


def test_invoice_totals_match_hand_calculation(scenario):
    assert scenario["i1"].total == D("243.00") and scenario["i1"].tax_total == D("18.00")
    assert scenario["i2"].total == D("180.00") and scenario["i3"].total == D("110.00")


def test_sales_summary_monthly(scenario):
    r = q.sales_summary(JAN1, FEB28, "month")
    assert r["rows"][0] == [
        "2025-01",
        2,
        D("430.00"),
        D("25.00"),
        D("18.00"),
        D("0.00"),
        D("423.00"),
        D("99.00"),
        D("324.00"),
    ]
    assert r["rows"][1] == [
        "2025-02",
        1,
        D("100.00"),
        D("0.00"),
        D("10.00"),
        D("0.00"),
        D("110.00"),
        D("0.00"),
        D("110.00"),
    ]
    assert r["totals"] == [
        "Total",
        3,
        D("530.00"),
        D("25.00"),
        D("28.00"),
        D("0.00"),
        D("533.00"),
        D("99.00"),
        D("434.00"),
    ]


@pytest.mark.parametrize(
    "period,n_rows", [("day", 3), ("week", 3), ("month", 2), ("quarter", 1), ("year", 1)]
)
def test_sales_summary_periods_add_up(scenario, period, n_rows):
    r = q.sales_summary(JAN1, FEB28, period)
    assert len(r["rows"]) == n_rows and r["totals"][6] == D("533.00") and r["totals"][8] == D("434.00")


def test_sales_by_product_revenue_cost_profit(scenario):
    rows = {r[0]: r for r in q.sales_by(JAN1, FEB28, "product")["rows"]}
    # name, sku, units, revenue(ex-tax), tax, cost, gross profit
    assert rows["Alpha"][2:] == [3, D("280.00"), D("28.00"), D("180.00"), D("100.00")]
    assert rows["Beta"][2:] == [5, D("225.00"), D("0.00"), D("100.00"), D("125.00")]


def test_top_products_by_units(scenario):
    rows = q.sales_by(JAN1, FEB28, "product", limit=1, order="qty")["rows"]
    assert [r[0] for r in rows] == ["Beta"]


def test_sales_by_category_and_customer(scenario):
    cat = q.sales_by(JAN1, FEB28, "category")["rows"]
    assert cat[0][1] == 8 and cat[0][2] == D("505.00")
    cust = {r[0]: r[2] for r in q.sales_by(JAN1, FEB28, "customer")["rows"]}
    assert cust == {"Cust A": 4, "Second": 4}


def test_tax_summary(scenario):
    rows = {r[0]: r for r in q.tax_summary(JAN1, FEB28)["rows"]}
    assert rows["10.00%"][1:] == [2, D("280.00"), D("28.00")] and rows["0.00%"][1:] == [
        2,
        D("225.00"),
        D("0.00"),
    ]


def test_discounts_report(scenario):
    r = q.discounts(JAN1, FEB28)
    assert dict(r["summary"]) == {
        "Invoice-level discounts": D("25.00"),
        "Line-level discounts": D("20.00"),
        "Total given": D("45.00"),
    }


def test_payment_methods_and_daily_collections(scenario):
    r = q.payment_methods(JAN1, FEB28)
    assert {row[0]: row[3] for row in r["rows"]} == {"Cash": D("143.00"), "UPI": D("180.00")}
    d = q.daily_collections(JAN1, FEB28)
    assert d["rows"] == [[date(2025, 1, 10), D("143.00"), D("180.00"), D("323.00")]]


def test_receivables_with_ageing(scenario):
    r = q.receivables(as_of=date(2025, 3, 1))
    # Inv1 due 243 - 99 returned - 143 paid = 1.00 (age 50d); Inv3 due 110.00 (age 24d); Inv2 settled; Inv4 cancelled
    row = r["rows"][0]
    assert row[0] == "Cust A" and row[2:] == [D("110.00"), D("1.00"), D("0.00"), D("0.00"), D("111.00")]
    assert len(r["rows"]) == 1


def test_payables_and_purchases(manager, product, supplier):
    from apps.purchasing.services import pay_supplier_invoice, quick_receive

    _, inv = quick_receive(product=product, quantity=10, user=manager, unit_cost=D("50"))  # 500.00
    pay_supplier_invoice(inv.pk, amount=D("150"), method="cash", paid_on=date(2025, 1, 5), user=manager)
    r = q.payables()
    assert r["rows"] == [["ABC", "ABC Pvt", 1, D("500.00"), D("150.00"), D("350.00")]]


def test_returns_report(scenario):
    r = q.returns_report(JAN1, FEB28)
    assert r["rows"] == [["Alpha", "Resellable - back to stock", 1, D("99.00")]]


def test_inventory_valuation_low_stock_and_ageing(scenario):
    # Alpha: 100 - 2 - 1 + 1 returned = 98 @ 60 = 5880 ; Beta: 100 - 1 - 4 = 95 @ 20 = 1900   (cancelled sale restored)
    v = q.inventory_valuation()
    assert v["totals"][3] == 193 and v["totals"][5] == D("7780.00")
    low = q.low_stock()
    assert [r[1] for r in low["rows"]] == ["Beta"] and low["rows"][0][7] == "Low"
    ag = q.stock_ageing()
    assert ag["totals"][-1] == 193


def test_reconciliation_report_is_clean_for_valid_data(scenario):
    assert q.reconciliation()["rows"] == []


def test_reconciliation_flags_corrupted_data(scenario):
    from apps.sales.models import Invoice

    Invoice.objects.filter(pk=scenario["i1"].pk).update(total_paid=D("999.00"))
    kinds = {r[0] for r in q.reconciliation()["rows"]}
    assert {"Invoice paid mismatch", "Overpaid invoice"} <= kinds


def test_dashboard_numbers_come_from_the_database(scenario):
    d = q.dashboard_data(today=date(2025, 2, 6))
    assert d["sales_month"] == D("110.00") and d["orders_month"] == 1
    assert d["collected_month"] == D("0.00") and d["receivable"] == D("111.00")
    assert d["n_low"] == 1 and d["n_out"] == 0
