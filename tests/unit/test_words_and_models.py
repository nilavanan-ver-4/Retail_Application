from decimal import Decimal as D

import pytest
from django.db import IntegrityError, transaction

from apps.core.money import q2, to_decimal
from apps.core.words import amount_in_words
from apps.products.models import Product


@pytest.mark.parametrize(
    "amount,text",
    [
        ("0", "Zero Ringgit only"),
        ("1", "One Ringgit only"),
        ("30000", "Thirty Thousand Ringgit only"),
        ("218.70", "Two Hundred Eighteen Ringgit and Seventy Sen only"),
        ("1000000.01", "One Million Ringgit and One Sen only"),
        ("21", "Twenty-One Ringgit only"),
    ],
)
def test_amount_in_words(amount, text):
    assert amount_in_words(D(amount)) == text


def test_money_helpers():
    assert q2(D("2.675")) == D("2.68") and q2(D("2.665")) == D("2.67") and q2(D("-1.005")) == D("-1.01")
    assert to_decimal("abc") is None and to_decimal("NaN") is None and to_decimal("Infinity") is None
    assert to_decimal(" 12.5 ") == D("12.5") and to_decimal("", D("0")) == D("0")


@pytest.mark.parametrize(
    "stock,reorder,level",
    [
        (100, 20, 5),
        (17, 20, 4),
        (10, 20, 3),
        (5, 20, 2),
        (4, 20, 1),
        (0, 20, 1),
        (5, 0, 5),
        (19, 20, 4),
        (16, 20, 3),
    ],
)
def test_legacy_availability_scale(stock, reorder, level):
    """Mirrors DB trigger update_inventory_availability_scale (ratio thresholds 1 / .85 / .5 / .25)."""
    assert Product(stock_on_hand=stock, reorder_level=reorder).availability_level == level


def test_profit_percentage_matches_legacy_formula():
    p = Product(cost_price=D("50"), selling_price=D("70"))
    assert p.unit_profit == D("20") and p.profit_percentage == D("40.00")
    assert Product(cost_price=D("0"), selling_price=D("5")).profit_percentage is None


@pytest.mark.django_db
def test_database_constraints_reject_bad_rows(category):
    base = dict(name="x", category=category, unit="pcs", cost_price=1, selling_price=2)
    for bad in ({"selling_price": -1}, {"tax_rate": 101}, {"cost_price": -1}):
        with pytest.raises(IntegrityError), transaction.atomic():
            Product.objects.create(sku="C" + str(len(bad)) + list(bad)[0], **{**base, **bad})
    Product.objects.create(sku="DUP", **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.create(sku="dup", **base)  # case-insensitive unique SKU


@pytest.mark.django_db
def test_barcode_unique_but_blank_allowed(category):
    base = dict(name="x", category=category, unit="pcs", cost_price=1, selling_price=2)
    Product.objects.create(sku="B1", barcode="123", **base)
    Product.objects.create(sku="B2", barcode="", **base)
    Product.objects.create(sku="B3", barcode="", **base)
    with pytest.raises(IntegrityError), transaction.atomic():
        Product.objects.create(sku="B4", barcode="123", **base)
