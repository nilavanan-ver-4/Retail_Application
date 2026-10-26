"""Pure invoice arithmetic - no database, no Django. Fully unit-tested.

Documented rules (docs/BUSINESS_RULES.md):

1. gross        = unit_price x quantity
2. net          = gross - line_discount            (line discount is an AMOUNT, as in the legacy app)
3. subtotal     = sum(net)                         (legacy ``invoice.total_amount``)
4. discount     = round_half_up(subtotal x pct/100), or a fixed amount; must be <= subtotal
                                                   (legacy ``invoice.discount``)
5. The invoice discount is allocated to lines in proportion to their net value;
   the last line absorbs the rounding remainder so allocations sum exactly.
6. taxable      = net - allocated_discount         (discounts reduce the taxable value)
7. tax          = round_half_up(taxable x rate/100) per line
8. total        = subtotal - discount + tax_total + shipping   (legacy ``final_amount`` when tax=0)

Every intermediate value is quantized to 2 dp (ROUND_HALF_UP). No floats.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from apps.core.exceptions import BusinessRuleError
from apps.core.money import ZERO, pct_of, q2


@dataclass
class LineInput:
    quantity: int
    unit_price: Decimal
    discount: Decimal = ZERO
    tax_rate: Decimal = ZERO


@dataclass
class LineResult:
    quantity: int
    unit_price: Decimal
    discount: Decimal
    tax_rate: Decimal
    gross: Decimal
    net: Decimal
    allocated_discount: Decimal = ZERO
    taxable: Decimal = ZERO
    tax: Decimal = ZERO
    total: Decimal = ZERO


@dataclass
class InvoiceTotals:
    lines: list = field(default_factory=list)
    subtotal: Decimal = ZERO
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO
    shipping: Decimal = ZERO
    tax_total: Decimal = ZERO
    total: Decimal = ZERO


def compute_invoice(lines, *, discount_percent=ZERO, discount_amount=None, shipping=ZERO) -> InvoiceTotals:
    if not lines:
        raise BusinessRuleError("An invoice needs at least one line.")
    discount_percent = Decimal(discount_percent or 0)
    shipping = q2(shipping or 0)
    if shipping < 0:
        raise BusinessRuleError("Shipping charge cannot be negative.")
    if not (Decimal(0) <= discount_percent <= Decimal(100)):
        raise BusinessRuleError("Discount percentage must be between 0 and 100.")
    if discount_amount is not None and discount_percent:
        raise BusinessRuleError("Provide either a discount percentage or a discount amount, not both.")

    results = []
    for i, ln in enumerate(lines, start=1):
        if int(ln.quantity) != ln.quantity or ln.quantity <= 0:
            raise BusinessRuleError(f"Line {i}: quantity must be a positive whole number.")
        price = q2(ln.unit_price)
        if price < 0:
            raise BusinessRuleError(f"Line {i}: unit price cannot be negative.")
        disc = q2(ln.discount or 0)
        if disc < 0:
            raise BusinessRuleError(f"Line {i}: discount cannot be negative.")
        gross = q2(price * ln.quantity)
        if disc > gross:
            raise BusinessRuleError(f"Line {i}: discount {disc} exceeds the line amount {gross}.")
        rate = Decimal(ln.tax_rate or 0)
        if not (Decimal(0) <= rate <= Decimal(100)):
            raise BusinessRuleError(f"Line {i}: tax rate must be between 0 and 100.")
        results.append(LineResult(ln.quantity, price, disc, rate, gross, gross - disc))

    subtotal = sum((r.net for r in results), ZERO)
    if discount_amount is not None:
        disc_total = q2(discount_amount)
        discount_percent = (disc_total / subtotal * 100).quantize(Decimal("0.01")) if subtotal else ZERO
    else:
        disc_total = pct_of(subtotal, discount_percent)
    if disc_total < 0:
        raise BusinessRuleError("Discount cannot be negative.")
    if disc_total > subtotal:
        raise BusinessRuleError(f"Invoice discount {disc_total} exceeds the subtotal {subtotal}.")

    # allocate the invoice discount across lines, last line takes the remainder
    remaining = disc_total
    for idx, r in enumerate(results):
        if idx == len(results) - 1:
            alloc = remaining
        elif subtotal:
            alloc = min(q2(disc_total * r.net / subtotal), remaining)
        else:
            alloc = ZERO
        r.allocated_discount = alloc
        remaining -= alloc
        r.taxable = r.net - alloc
        r.tax = pct_of(r.taxable, r.tax_rate)
        r.total = r.taxable + r.tax

    tax_total = sum((r.tax for r in results), ZERO)
    total = subtotal - disc_total + tax_total + shipping
    return InvoiceTotals(results, subtotal, discount_percent, disc_total, shipping, tax_total, total)


def refund_value(line_total: Decimal, line_quantity: int, return_quantity: int,
                 already_returned_quantity: int, already_refunded: Decimal) -> Decimal:
    """Value credited for returning ``return_quantity`` units of an invoice line.

    Pro-rata of the line's final total (taxable + tax, i.e. after discounts); when
    the last units come back the remaining value is credited so that rounding
    never leaves a residue or over-refunds.
    """
    if return_quantity <= 0:
        raise BusinessRuleError("Return quantity must be positive.")
    if already_returned_quantity + return_quantity > line_quantity:
        raise BusinessRuleError("Cannot return more than was sold.")
    if already_returned_quantity + return_quantity == line_quantity:
        return q2(line_total - already_refunded)
    return q2(line_total * return_quantity / line_quantity)
