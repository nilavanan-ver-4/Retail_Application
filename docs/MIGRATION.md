# Flask → Django migration plan and mapping

## 1. Strategy: side-by-side schemas, copy with verification, Flask stays as rollback

The task asked for in-place adoption (`db_table`, `inspectdb`) where sensible. After reading the triggers this
was **deliberately not done**, for these reasons:

1. The legacy tables carry *behaviour*: ten triggers that move stock, create payments and ledger rows, and
   `GENERATED` columns. Adopting the tables in place would leave those triggers firing underneath the new Django
   services and **double-count stock and money** (the legacy triggers already double-count on product insert ✔).
   Removing them means dropping objects in a database that may hold valuable data — which was not authorised.
2. Several legacy columns/types cannot satisfy the new integrity rules (CHECK constraints, Decimal(12,2),
   case-insensitive unique SKU, immutable ledgers) without destructive `ALTER`s.
3. A copy keeps the Flask application fully functional against its untouched database — a genuine rollback.

So: new Django-owned tables (`products_product`, `sales_invoice`, …) live in the target database; the legacy
database is opened **read-only** (`default_transaction_read_only=on`) by `manage.py import_legacy`, which copies
the data **preserving primary keys**, advances sequences, and verifies counts and money totals. Nothing is
dropped, truncated or modified in the legacy database, and uploaded files are untouched (the legacy app stored
no product images; its `uploads/` folder only ever held transient import files).

Is there valuable data? Unknown from the repository — treat it as production data. Hence: backup, rehearse on a
staging copy, review the report, keep Flask until acceptance.

## 2. Table / column mapping

| Legacy | Django table (model) | Notes |
|---|---|---|
| `supplier` (`id`, `name`, `company`, `mobile`, `mail`, `terms`, `timestamp`) | `suppliers_supplier` | `id` kept; `mail`→`email`; `timestamp`→`created_at` |
| `add_product` ⟕ `inventory` | `products_product` + `products_category` | `pro_id`→`id` **and** `sku=str(pro_id)`; `pro_name`→`name`; `safety_level`→`reorder_level`; `hc_code`→`hs_code`; `terms_from_invoice`→`supplier_terms`; stock = `inventory.quantity` (authoritative), becomes `stock_on_hand` + one `opening` movement; `net_profit`/`profit_percentage` are now computed properties; free-text `category` → `Category` rows (case-insensitive merge); `tax_rate`=0 (legacy stored none) |
| `customer` | `customers_customer` | `id` kept; `address`→`billing_address`; `time`→`created_at`; mobile stays unique |
| `invoice` | `sales_invoice` | `invoice_no`→`id` and `number`; `total_amount`→`subtotal`; `discount`→`discount_amount` (+ derived `discount_percent`); `final_amount`→`total`; `sale_by`→`sold_by`; `is_legacy=true` |
| `invoice_products` | `sales_invoiceline` | `id` kept; `price`→`unit_price`; generated `total`→`net_amount`; invoice discount re-allocated per line (needed for correct return values); cost snapshot = product's *current* cost (estimate) |
| `payment` | `payments_payment` | `id` kept; `Paid`/`Completed`→`completed`, `Pending`→`pending` (placeholder, excluded from received); `payment_mode`→`method` (`legacy_mode` keeps the raw value) |
| `invoice.total_paid` vs payment rows | reconciling `payment` row | if `total_paid` exceeds recorded payments a clearly labelled reconciling payment is created, and reported |
| `ledger` | `customers_ledgerentry` | `id` kept, `source='legacy'`, stored balance kept as-is; customer resolved by mobile |
| `add_item` | `purchasing_goodsreceipt` (+line) | `LEGACY-GRN-<item_id>`, status confirmed, **no stock movement** (already in stock) |
| `supplier_invoice` | `purchasing_supplierinvoice` (+line) | `invoice_no` kept; `Paid`→reconciling `supplierpayment`; year sequences advanced |
| `sale_product` (only in `create.sql`) | – | unused by code; not imported |

New tables with no legacy source: users/groups, `audit_log`, `inventory_stockmovement`, `inventory_stocktake*`,
purchase orders, `sales_return*`, `core_document_sequence`, `core_import_job`.

Key-preservation check: `tests/characterization/test_legacy_equivalence.py::test_import_of_real_legacy_output_preserves_everything`.

## 3. Procedure and checklist

1. ☐ **Freeze**: stop writes to Flask (maintenance window). Keep Flask running read-only if desired.
2. ☐ **Backup** the legacy DB: `pg_dump -Fc -f legacy_$(date +%F).dump <legacy db>`; copy off-host. Rehearse restore into a scratch DB.
3. ☐ **Staging rehearsal**: restore that dump to a staging server; create the Django DB; `migrate`, `setup_roles`.
4. ☐ `import_legacy --dry-run --report r.json` (loads everything, verifies, **rolls back**). Fix `quarantine` entries; read every `anomalies` entry (esp. `overpaid_invoice`, `stock_mismatch`, `paid_without_payment_rows`).
5. ☐ Real import on staging; `python manage.py data_snapshot`; compare with source: counts, Σ invoice totals, Σ completed payments, stock units, stock value (the report's `totals`). The importer fails if they differ.
6. ☐ **Acceptance** (§6) on staging with real users.
7. ☐ Repeat 3–5 on production, `scripts/backup.sh` + `scripts/verify_restore.sh` of the new DB, go live.
8. ☐ Keep Flask + the legacy dump for ≥ 30 days. **Rotate the old DB password and Flask secret** (they are in Git history).

Rollback (any time before new data matters): stop Django, start `legacy_flask/app.py` against the untouched legacy DB. After cut-over, new Django-only transactions are not in the legacy DB (see §7).

## 4. Where each trigger's behaviour went

| Legacy trigger | Django |
|---|---|
| `add_product_to_inventory` / `update_inventory_from_add_product` (stock on insert — **double-applied**) | `ProductCreateView`: opening stock posted once as an `opening` movement |
| `update_inventory_on_add_item` | `purchasing.services.confirm_receipt` → `apply_movement(purchase)` |
| `update_add_product_on_inventory_change` (sync copies) | single stock column; copy removed |
| `update_inventory_availability_scale` (1–5) | `Product.availability_level` (same thresholds 1 / .85 / .5 / .25, test-proven) |
| `update_payment_status` (BEFORE) | `payments.services.recalculate_invoice` (derives status from payment rows) |
| `update_payment_on_invoice` | `sales.services.create_invoice` → `record_payment` |
| `update_ledger_on_invoice` | `post_ledger_entry` (Debit on invoice, Credit per payment) |
| `trg_update_invoice_payment_status_*` (sums *all* payment rows incl. `Pending`) | recalculation counts only `completed` rows |

## 5. Intentional behaviour changes (legacy behaviour that was wrong or unsafe)

1. Totals, prices, tax come from the server; browser values ignored. Price override needs the *Override price* permission and is audited.
2. Partial payment ⇒ *Partially Paid* (legacy: *Paid*). Overpayment is refused (legacy accepted it).
3. Stock can't go negative (DB `CHECK` + row locks). Legacy: unchecked `UPDATE`.
4. Product "delete" is **archive**; history is preserved (legacy cascaded and destroyed invoice lines).
5. Products may be sold at any stock > 0; the legacy rule "availability ≥ 3 to be listed on the billing screen" (a derived 1–5 scale) hid sellable items — the 1–5 level is still shown for information.
6. Manual ledger `Credit`/`Debit` now follow one convention (Debit = customer owes more). Legacy `add_ledger` inverted it and was non-functional.
7. Invoice numbers continue from the legacy sequence (≥ 100000) via a locked counter; zero-value invoices are rejected.
8. Product import: existing SKU is an error unless *update* mode is chosen (legacy silently added quantity); failures are reported per row, never as "success".
9. Tax: the legacy database stored none. `tax_rate` defaults to **0**; set per product from a verified rule. No GST rate is invented.
10. Dashboard "pending payments" (legacy query could never match) is replaced by real unpaid/receivable figures.

## 6. Acceptance checklist

Login/roles · create product + opening stock · receive stock (draft → confirm) · sale with split payment ·
printed/PDF invoice looks right · cancel invoice (stock restored) · partial payment/reversal · return + refund ·
import (preview → apply) · reports for a known day equal Flask's dashboard for the same day · low-stock list ·
backup + restore verification passes · `check --deploy` clean.

## 7. Assumptions and known limitations

* Legacy naive timestamps are in `TIME_ZONE` (default `Asia/Kuala_Lumpur`; currency RM per the legacy print page).
* Legacy invoice-line costs are unknown → profit on migrated invoices uses current cost (labelled as an estimate).
* Quantities stay integers (legacy `INT`); fractional units are deferred.
* The old and new systems are not kept in sync after cut-over (one-way migration). A second delta import is **not** supported (the importer refuses a non-empty target by design).
* Legacy `payment` rows without a matching invoice cannot exist (FK) — none are expected; orphans in other tables are reported, not dropped silently.
* If the legacy `invoice` table lacks `payment_method` (older schema) the importer detects it.
