# Feature comparison: Flask → Django

✔ done and tested · ◐ partly · ✖ deferred

| Area | Flask (legacy) | Django |
|---|---|---|
| Login, roles, password change/reset, lock-out, session expiry | ✖ none | ✔ 6 roles, server-enforced; axes lock-out; 1 h single-use reset links |
| User administration, activation | ✖ | ✔ (admin creates users; self-sign-up intentionally absent for an internal system) |
| Products CRUD | ✔ (hard delete) | ✔ archive/restore; SKU, barcode, brand, category, image, HS code, tax rate, expiry, reorder level |
| Product import | ✔ silent | ✔ preview, row errors, duplicate SKU detection, create/update modes, Excel + CSV, legacy headers accepted |
| Product export / bulk update | ✖ | ✔ CSV (cost only with permission) / bulk archive, category, tax, reorder, price % |
| Inventory list, availability 1–5 | ✔ | ✔ + low/out/expiring filters, valuation |
| Stock ledger, adjustments, damage write-off, stock-take, reconciliation, ageing | ✖ | ✔ |
| Customers CRUD, import | ✔ | ✔ + shipping address, credit limit, archive, export, outstanding balance, history |
| Customer ledger | ◐ (broken manual entry) | ✔ system-generated + audited manual entries; legacy rows imported |
| Suppliers CRUD, import | ✔ | ✔ + payables, history |
| Add stock + supplier invoice | ✔ | ✔ same flow (`YYYY00001`) **and** PO → receipt → invoice → payment |
| Purchase orders, partial receipts, backorders (outstanding qty) | ✖ | ✔ |
| Billing (customer, search, qty, discounts, totals, payment) | ✔ trusts browser | ✔ server-authoritative, keyboard shortcuts, split payment, credit-limit check, double-submit protection |
| Tax | ✖ (print-only 10 %) | ✔ per-product rate, per-line tax (default 0); GST split (CGST/SGST) ✖ |
| Invoice print / PDF | ◐ browser text dump | ✔ branded HTML + WeasyPrint PDF, amount in words |
| Invoice search / filters / cancel / copy | ◐ list only | ✔ |
| Payments list | ✔ | ✔ filters, summaries, CSV, reversal, refunds |
| Returns / credit notes | ✖ | ✔ approval, resellable vs damaged, refund |
| Dashboard | ✔ 14 widgets | ✔ real data, SVG charts, role-aware |
| Reports | dashboard only | ✔ 19 reports, date ranges, CSV/Excel/PDF |
| Audit log | ✖ | ✔ immutable; prices, stock, cancellations, payments, roles, logins, exports, imports |
| REST API + OpenAPI | ✖ | ✔ products, customers, invoices, stock movements; token/session auth, pagination, throttling |
| Responsive, accessible UI | ◐ | ✔ mobile/tablet/desktop, labels, focus, skip link, reduced motion, dark mode, print CSS |
| Backups / restore verification / CI / Docker | ✖ | ✔ (Docker image build not executed here — see TEST_REPORT) |

## Deliberately deferred (needs business decisions or data the system does not have)

| Item | Why |
|---|---|
| Product variants (size/colour) | No variant data in legacy; model SKU-per-variant works today, a parent/variant structure is a larger design |
| Reserved stock | No orders-before-sale workflow exists |
| Stock transfers | No locations/branches in the data |
| Customer-specific pricing | No price lists exist; credit limits are done |
| Advance payments / customer credit balances | Overpayment is refused until the accounting treatment is decided |
| GST/SST invoice rules (CGST/SGST split, HSN-driven rates, tax-invoice wording) | "Verified business rules rather than invented rates" — supply the rules; the per-line tax engine is ready |
| Cash rounding (e.g. RM 0.05) | Not in legacy; needs a policy |
| Fractional quantities (kg) | Legacy integers; needs a decision on units |
| Net profit | No expense data |
| Payment-gateway integration | Business uses cash/bank/UPI/card references; no gateway credentials supplied |
| Multi-business-unit data isolation | Single business in the data model |
| Optional 3-D dashboard visualisation | Data supports only per-category stock; omitted to keep the essential workflows lightweight |
| Two-way / delta sync with the old DB | One-way cut-over by design |
