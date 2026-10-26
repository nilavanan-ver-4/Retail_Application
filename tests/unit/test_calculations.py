from decimal import Decimal as D

import pytest

from apps.core.exceptions import BusinessRuleError
from apps.sales.calculations import LineInput, compute_invoice, refund_value


def L(qty, price, disc="0", tax="0"):
    return LineInput(qty, D(price), D(disc), D(tax))


def test_legacy_example_matches_recorded_flask_totals():
    # legacy golden scenario: 3x70 (disc 10) + 1x45 (disc 2), 10% invoice discount
    t = compute_invoice([L(3, "70", "10"), L(1, "45", "2")], discount_percent=D("10"))
    assert t.subtotal == D("243.00")
    assert t.discount_amount == D("24.30")
    assert t.total == D("218.70")
    assert t.tax_total == D("0.00")


def test_no_discount_no_tax():
    t = compute_invoice([L(2, "70")])
    assert (t.subtotal, t.discount_amount, t.total) == (D("140.00"), D("0.00"), D("140.00"))


def test_rounding_half_up_on_percentage_discount():
    # 33.35 * 15% = 5.0025 -> 5.00 ; 0.05*10% = 0.005 -> 0.01 (half up)
    assert compute_invoice([L(1, "33.35")], discount_percent=D("15")).discount_amount == D("5.00")
    assert compute_invoice([L(1, "0.05")], discount_percent=D("10")).discount_amount == D("0.01")


def test_tax_applies_after_discount_and_totals_reconcile():
    t = compute_invoice([L(1, "100", tax="6"), L(1, "50", tax="10")], discount_percent=D("10"))
    # discount 15.00 allocated 10.00 / 5.00 -> taxable 90 & 45 -> tax 5.40 + 4.50
    assert [r.allocated_discount for r in t.lines] == [D("10.00"), D("5.00")]
    assert [r.tax for r in t.lines] == [D("5.40"), D("4.50")]
    assert t.tax_total == D("9.90")
    assert t.total == D("150.00") - D("15.00") + D("9.90")
    assert sum(r.total for r in t.lines) == t.total  # no shipping


def test_discount_allocation_sums_exactly_with_awkward_split():
    t = compute_invoice([L(1, "10.00"), L(1, "10.00"), L(1, "10.00")], discount_amount=D("1.00"))
    assert sum(r.allocated_discount for r in t.lines) == D("1.00")
    assert sum(r.taxable for r in t.lines) == t.subtotal - t.discount_amount


def test_shipping_added_untaxed():
    t = compute_invoice([L(1, "100", tax="10")], shipping=D("5.00"))
    assert t.total == D("115.00") and t.tax_total == D("10.00")


def test_decimal_not_float_artifacts():
    t = compute_invoice([L(3, "0.10")])  # 0.1*3 in float = 0.30000000000000004
    assert t.subtotal == D("0.30")


@pytest.mark.parametrize(
    "kwargs,msg",
    [
        (dict(lines=[L(1, "10", "11")]), "exceeds the line amount"),
        (dict(lines=[L(0, "10")]), "positive whole number"),
        (dict(lines=[L(-1, "10")]), "positive whole number"),
        (dict(lines=[L(1, "-1")]), "price cannot be negative"),
        (dict(lines=[L(1, "10", "-1")]), "discount cannot be negative"),
        (dict(lines=[L(1, "10", tax="101")]), "tax rate"),
        (dict(lines=[L(1, "10")], discount_percent=D("101")), "between 0 and 100"),
        (dict(lines=[L(1, "10")], discount_amount=D("10.01")), "exceeds the subtotal"),
        (dict(lines=[L(1, "10")], discount_percent=D("5"), discount_amount=D("1")), "not both"),
        (dict(lines=[L(1, "10")], shipping=D("-1")), "Shipping"),
        (dict(lines=[]), "at least one line"),
    ],
)
def test_invalid_inputs_rejected(kwargs, msg):
    lines = kwargs.pop("lines")
    with pytest.raises(BusinessRuleError, match=msg):
        compute_invoice(lines, **kwargs)


def test_hundred_percent_discount_gives_zero_total():
    assert compute_invoice([L(1, "10")], discount_percent=D("100")).total == D("0.00")


def test_refund_value_prorata_and_last_unit_takes_remainder():
    total = D("100.00")  # 3 units -> 33.33, 33.33, then remainder 33.34
    a = refund_value(total, 3, 1, 0, D("0"))
    b = refund_value(total, 3, 1, 1, a)
    c = refund_value(total, 3, 1, 2, a + b)
    assert (a, b, c) == (D("33.33"), D("33.33"), D("33.34"))
    assert a + b + c == total


def test_refund_value_rejects_over_return():
    with pytest.raises(BusinessRuleError):
        refund_value(D("10"), 2, 2, 1, D("5"))
    with pytest.raises(BusinessRuleError):
        refund_value(D("10"), 2, 0, 0, D("0"))
