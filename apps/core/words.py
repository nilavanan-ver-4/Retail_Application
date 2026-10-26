"""Amount in words for invoices (replaces the hard-coded text in the legacy print page)."""

from decimal import Decimal

ONES = [
    "",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]
SCALES = [(10**9, "Billion"), (10**6, "Million"), (10**3, "Thousand")]


def _below_1000(n):
    parts = []
    if n >= 100:
        parts.append(ONES[n // 100] + " Hundred")
        n %= 100
    if n >= 20:
        parts.append(TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else ""))
    elif n:
        parts.append(ONES[n])
    return " ".join(parts)


def integer_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    for size, name in SCALES:
        if n >= size:
            parts.append(f"{_below_1000(n // size)} {name}")
            n %= size
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts)


def amount_in_words(amount, currency="Ringgit", minor="Sen") -> str:
    amount = Decimal(amount).quantize(Decimal("0.01"))
    whole, cents = int(amount), int((amount - int(amount)) * 100)
    text = f"{integer_words(whole)} {currency}"
    if cents:
        text += f" and {integer_words(cents)} {minor}"
    return text + " only"
