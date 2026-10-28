# Technical audit of the Flask application

Scope: every file in the original repository (`app.py` 1,709 lines, `DB/*.sql|py`, 25 templates, static
assets, `uploads/`, `requirements.txt`, README). The code was read in full and **executed**: the unmodified
Flask app was run against a PostgreSQL database built from `DB/table.sql` + `DB/trg.sql`
(`tests/characterization/legacy_char.py`; recorded in `legacy_golden.json`). Findings marked ✔ were reproduced
by that run.

## 1. Feature inventory and route mapping

| # | Flask route(s) | Purpose | Django equivalent |
|---|---|---|---|
| 1 | `/` | Dashboard (14 raw SQL widgets) | `core:dashboard` (`reports.queries.dashboard_data`) |
| 2 | `/customers` (GET list, POST add) | Customer list / quick add | `customers:list`, `customers:create` |
| 3 | `/add_customer`, `/update_customer/<id>` | Add / edit customer | `customers:create`, `customers:update` |
| 4 | `/import_customers` | CSV/Excel import | `customers:import` (+ preview) |
| 5 | `/get_customer`, `/get_customer_details/<mobile>` | Lookup / auto-create by mobile | `customers:lookup` (JSON); auto-create kept inside invoice creation |
| 6 | `/suppliers`, `/add_supplier`, `/edit_supplier/<id>` | Supplier CRUD | `suppliers:list/create/update/detail` |
| 7 | `/import_suppliers` | Import | `suppliers:import` (+ preview) |
| 8 | `/get_suppliers`, `/get_supplier_name/<id>` | JSON helpers for forms | replaced by `<select>` of suppliers |
| 9 | `/products` (list/add), `/add_product`, `/update_product/<id>` | Product CRUD | `products:list/create/update/detail` |
| 10 | `/delete_product/<id>` | **Hard delete** (cascades to invoice lines) | `products:archive` / `restore` (history preserved) |
| 11 | `/import_products` | Import; existing id ⇒ quantity += | `products:import` (mode *create* or *update*; qty becomes a stock movement) |
| 12 | `/inventory` | Stock list with 1–5 availability | `inventory:stock` (+ movements, reconcile, stock-take) |
| 13 | `/add_item?product_id=` | Add stock + auto supplier invoice `YYYY00001` | `purchasing:quick_receive` (same invoice-number format) |
| 14 | `/create_invoice` (GET/POST) | Billing | `sales:create` (billing screen) |
| 15 | `/get_product_suggestions/<q>`, `/get_product_details` | Product autocomplete | `sales:product_search` (JSON, no cost) |
| 16 | `/get_invoice_details` | Invoice JSON | `sales:detail`, REST `/api/v1/invoices/` |
| 17 | `/invoices` | Invoice list | `sales:list` (filters, totals) |
| 18 | `/payments` | Payment list | `payments:list` (+ CSV) |
| 19 | `/ledger`, `/add_ledger`, `/import_ledger` | Customer ledger, manual entry | `customers:ledger`, `customers:ledger_add` (import not carried: see §3) |
| 20 | `/filters`, `/order_history` | Placeholder pages (`order_history.html` does not exist ✔) | superseded by list filters |
| 21 | 404 handler | | `handler403/404/500` |
| – | `templates/print_invoice.html` | Browser-side print (localStorage), hard-coded 10 % GST and "RM Thirty Thousand only" | `sales:print`, `sales:pdf` (WeasyPrint) with real figures |
| – | `templates/pricing.html` (unrouted) | Pricing table | Product list/detail (margin, profit %) |
| – | DB triggers (`DB/trg.sql`) | stock sync, availability scale, payment/ledger inserts | re-implemented as tested services (see MIGRATION.md §4) |

**Legacy database (10 tables):** `customer`, `supplier`, `add_product`, `inventory`, `add_item`, `invoice`,
`invoice_products`, `supplier_invoice`, `payment`, `ledger`. `DB/create.sql` additionally defines `sale_product`
(never used by the code). `DB/triggers.sql` (850 lines) and `DB/trg.sql` (404 lines) overlap; it is not possible
to tell from the repository which was deployed (documented risk; the importer reads the data, not the triggers).

## 2. Functionality that was incomplete or broken (✔ = reproduced)

| Defect | Evidence |
|---|---|
| ✔ **Stock doubled on product creation** — two `AFTER INSERT` triggers each add the quantity | product added with qty 100 → `inventory.quantity` = 200 |
| ✔ **Server trusts browser totals** — only checks that three client-supplied numbers agree | invoice for a 70.00 item stored as 10.00 |
| ✔ **"Paid" for any amount** — any paid method with `total_paid > 0` ⇒ `Paid`, even if partial or *larger than the invoice* | 218.70 recorded as Paid on a 10.00 invoice |
| ✔ `/add_ledger` always fails (`invoice_no` column missing in `table.sql`) | `column "invoice_no" … does not exist` |
| ✔ `/order_history` → HTTP 500 | `TemplateNotFound` |
| ✔ Add-product fails with an empty expiry date (`''` into a DATE) | psycopg2 error |
| Manual ledger "Credit" **increases** the balance whereas the trigger's "Credit" decreases it | `add_ledger` vs `update_ledger_on_invoice` |
| `update.sql` back-fills `Pending` rows into `payment` with the *full invoice amount*; the payment-sum trigger (in `triggers.sql`) would count them as received | `DB/update.sql` |
| No transactions: invoice header, lines and stock updates use separate connections — a failure mid-way leaves partial invoices | `execute_query` opens a new connection per statement |
| Invoice/product/supplier import swallow errors row by row and report "success" | `import_*` |
| Customer mobile unique constraint violated ⇒ silently ignored (`execute_query` returns `None`) | `add_customer_post` |
| `get_supplier_by_name` queries a column that does not exist | `supplier_name` |
| Hard-delete of a product cascades to `invoice_products`, destroying sales history | `ON DELETE CASCADE` |
| Printed invoice shows 10 % GST that is not stored anywhere; "RM Thirty Thousand only" is literal text | `print_invoice.html` |
| Floats for money in Python; `NUMERIC(10,2)` caps at 99,999,999.99 | `float(...)` throughout |

## 3. Missing retail capabilities (added or consciously deferred)

Added: authentication & roles, audit log, stock movement ledger with adjustments/stock-take/reconciliation,
purchase orders and goods receipts, supplier invoice vs. payment, split & partial payments, reversals, refunds,
returns/credit notes with approval, invoice cancellation, credit limits, 19 reports with CSV/Excel/PDF, REST API,
backups with restore verification, CI. **Deferred** (needs business input or data the system does not hold):
see [FEATURE_COMPARISON.md](FEATURE_COMPARISON.md). `import_ledger` is not carried over: its SQL targets
columns that do not exist and ledger history is imported from the old database instead.

## 4. Security findings in the Flask application

| ID | Severity | Finding |
|---|---|---|
| L-01 | **Critical** | No authentication or authorisation on any route — every page, import and delete is public |
| L-02 | **Critical** | Database superuser password (`postgres`/`1234`) and Flask `secret_key` hard-coded in `app.py` (in Git history — **rotate them**) |
| L-03 | **High** | Prices, discounts, totals and paid amounts taken from the browser (✔) |
| L-04 | High | `debug=True` in `app.run` — Werkzeug debugger allows remote code execution if exposed |
| L-05 | High | No CSRF protection on any POST (incl. `/delete_product`) |
| L-06 | Medium | Uploaded files written to `uploads/` with user-controlled names, never cleaned; content type not verified |
| L-07 | Medium | `print(...)` of full form data/PII to stdout |
| L-08 | Medium | Verbose error messages (`flash(f"Error …: {e}")`) leak SQL/internal details |
| L-09 | Low | Parameterised queries are used consistently (no SQL injection found) |
| L-10 | Low | No pinned dependency versions; `pyodbc` listed but unused; CDN script with placeholder Font-Awesome kit key |

## 5. Database design issues

Duplicated stock in `add_product.quantity` **and** `inventory.quantity` kept in sync by triggers (drift is
possible and was detected in testing); `safety_level` is `VARCHAR` in one table and `INT` in the other; no `CHECK`
on quantities or negative stock; payment table changed primary key mid-script; `ledger` links by mobile (text)
instead of customer id; money in `NUMERIC(10,2)`; timestamps are naive; `GENERATED` columns hide business logic;
orphans possible via `ON DELETE SET NULL`; no indexes beyond keys (the queries are small-scale).

## 6. Migration risks (and treatment)

| Risk | Treatment |
|---|---|
| Unknown which trigger file is live | Importer reads data only; stock is taken from `inventory.quantity`, mismatches with `add_product.quantity` are reported |
| Triggers double-applied side effects in legacy data | Anomaly report flags `stock_mismatch`, `overpaid_invoice`, `lines_do_not_sum`, `paid_without_payment_rows` |
| Rows that violate new constraints | Quarantined and listed; import aborts unless `--allow-quarantine` |
| `Pending` placeholder payments | Imported as `pending`, never counted as money received |
| Naive legacy timestamps | Interpreted in `TIME_ZONE` (assumption, configurable) |
| Fractional quantities | Legacy used integers; kept (deferred) |
| Loss of hard-deleted history | Not recoverable; archive replaces delete from now on |
