# Business rules (as implemented and tested)

## Money
* Python `Decimal` only; PostgreSQL `NUMERIC(12,2)`. Every amount is rounded **half-up to 2 dp** where it is computed (`apps/core/money.py`). No floats.
* Currency symbol/label and company details come from settings (`CURRENCY_SYMBOL`, `COMPANY_*`).

## Invoice arithmetic (`apps/sales/calculations.py`, 21 unit tests)
1. `gross = unit_price × quantity`; `net = gross − line_discount` (line discount is an **amount**, as in the legacy app; ≤ gross).
2. `subtotal = Σ net` (legacy `total_amount`).
3. Invoice discount = `subtotal × % / 100` (legacy: whole-invoice percentage), or a fixed amount; ≤ subtotal.
4. The invoice discount is allocated to lines in proportion to `net`; the last line takes the rounding remainder, so allocations sum exactly.
5. `taxable = net − allocated_discount`; `tax = taxable × rate / 100` per line (discounts reduce tax). Rate is the product's `tax_rate` (default 0 — **no rate is invented**).
6. `total = subtotal − discount + tax_total + shipping` (legacy `final_amount` when tax = shipping = 0).
7. The invariant `total = subtotal − discount + tax + shipping` and `line.net = price × qty − discount` are PostgreSQL `CHECK` constraints.
8. Total must be > 0. Shipping is untaxed and non-refundable. Cash rounding (e.g. RM 0.05) is **not** applied (deferred).
9. Prices come from the catalogue; only users with *Override price* may change them (audited as `invoice.price_override`).

## Invoice numbering and states
* Numbers: locked counter starting at 100000 (continues the legacy sequence); allocated inside the invoice transaction, so a rollback leaves no gap and two invoices can never share a number.
* Duplicate submissions: each billing form carries a one-time token; a repeat returns the already-created invoice.
* `status`: Pending → Completed (fully paid) · Returned (all goods credited) · Cancelled. `payment_status`: Unpaid / Partially Paid / Paid — always **derived from payment rows**.
* Cancel: reason required; money must already be reversed/refunded; blocked if returns exist; restores stock; ledger credited; audited. Corrections = cancel + "Copy as new".

## Stock
* `Product.stock_on_hand` changes only through `inventory.services.apply_movement`, under `SELECT … FOR UPDATE`, which also writes an immutable `StockMovement` (signed qty, balance after, reference, user). Types: opening, purchase, sale, sale_cancel, sale_return, return_damaged, adjustment, damage, count, import.
* No negative stock (DB `CHECK`), unless a product explicitly sets `allow_negative_stock`.
* Several products in one invoice are locked in primary-key order (no deadlocks). Concurrency is tested (10 buyers, 5 units ⇒ exactly 5 succeed).
* Goods enter stock **only** when a goods receipt is confirmed; confirmation is once-only; receiving more than a PO line's outstanding quantity is refused. The recorded unit cost updates the product cost (**costing method: latest purchase cost**, audited).
* Valuation = `on hand × cost_price`. Ageing = FIFO estimate from inbound movements. Reconciliation compares each balance with the sum of its movements.
* Stock-take: counts are compared with **live** stock when applied, so sales made during the count are not overwritten.
* Low stock: `stock ≤ reorder_level`; out of stock: `≤ 0`. Legacy 1–5 availability scale retained as `availability_level`.

## Payments, refunds, returns
* A payment ≤ the invoice's outstanding balance (no advances/credit balances). Multiple rows = split/partial payments. Methods: cash, card, bank transfer, UPI, other. Only a free-text reference is stored — never card numbers.
* `idempotency_key` makes a retried request return the original payment. The invoice row is locked, so concurrent payments cannot overpay.
* Reversal (permission, reason, audited) re-opens the balance and the customer debt. Refund ≤ net received.
* Return: cashier requests, manager approves. Validated against the original sale (cumulative quantity ≤ sold, including other pending returns). Credit = pro-rata of the line's final value (last unit takes the remainder). *Resellable* → back to stock; *damaged* → received then written off (two movements). If the customer has paid more than the new effective total, a refund (method chosen at approval) is issued in the same transaction.
* Balance due = `total − returned_total − net paid`.

## Customer ledger
Debit = customer owes more (invoice, refund paid out, payment reversal); Credit = owes less (payment, return credit, cancellation). Running balance = Σdebits − Σcredits, computed under a customer row lock. *Outstanding* (receivable) is computed from invoices, not from the ledger, so legacy ledger quirks cannot distort it.

## Reports (`apps/reports/queries.py`)
* **Invoiced sales** (revenue billed, incl. tax & shipping, excl. cancelled) ≠ **cash collected** (completed payments − refunds, by payment date). Never mixed.
* Line revenue = Σ taxable value. **Gross profit (estimate)** = revenue − qty × cost snapshot. **Net profit is not calculated**: no operating expenses exist in the system.
* Receivables aged 0-30/31-60/61-90/90+ from the invoice date. Payables = recorded supplier invoices not fully paid.
* Every report figure is verified against hand-computed totals in `tests/integration/test_report_accuracy.py`.

## Imports/exports
Parse → validate → **preview** → confirm. Invalid rows block the import unless the user ticks *skip invalid rows*; apply is one transaction and single-use. Only parsed rows are stored (the uploaded file itself is never written to disk). Limits: `.csv`/`.xlsx`, `MAX_UPLOAD_MB`, 5,000 rows. Exports neutralise spreadsheet formulas (`=`, `+`, `-`, `@`).
