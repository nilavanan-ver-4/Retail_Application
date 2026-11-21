# Retail application review — 21 November 2026

## Scope and evidence

Reviewed the active Django routes, templates/JavaScript/CSS, service layer, models and migrations, authentication/roles, REST serializers/permissions, imports, reports, legacy importer, tests, configuration, Docker/Nginx/Gunicorn deployment, GitHub workflows and recovery scripts. Flask and its SQL remain rollback/reference material, not the deployed application.

The working tree already contained uncommitted tax configuration and Windows startup work. Those changes were preserved. Earlier README/FEATURE_COMPARISON/SECURITY/TEST_REPORT claims were treated as historical claims, not current proof. This report supersedes blanket statements about feature completeness or absence of high-severity findings.

Business inference: one business, one stock pool, Malaysian address, RM, Kuala Lumpur time, integer quantities, latest-purchase-cost valuation, and customer credit sales. These defaults do not confirm actual jurisdiction, registration, turnover or accounting policies. No tax rate, payment credential, or accounting policy was invented.

**Verified** means exercised by tests/build/recovery in isolation. **Partial** means a working subset with a material gap. **Missing** means no implementation was found. **Configured** means code/config exists but the external service or production operation is unverified. Tests use synthetic data. Passing tests do not establish correctness for every possible input or concurrency interleaving.

Importance: essential, useful, optional, or not applicable to the inferred single-store business. Conditional classifications must change if the owner confirms relevant needs. Priorities: **P0** integrity/security/recovery blocker; **P1** essential operational completion; **P2** useful improvement; **P3** optional/future. “Fixed” identifies work done in this review.

## Architecture and traced workflows

Python 3.13, Django 5.2, DRF, PostgreSQL 16, psycopg, django-axes, django-filter, WeasyPrint and OpenPyXL; server-rendered Django templates and plain JavaScript/CSS. Runtime/dev requirements are pinned. There is no Node frontend build or configured static type checker.

| Workflow | UI → backend → persistence | Assessment |
|---|---|---|
| Sell | `templates/sales/create.html` → `static/js/invoice.js` → InvoiceCreateView or InvoiceViewSet → create_invoice → locked products → Decimal calculations → Invoice/InvoiceLine, StockMovement, customer LedgerEntry and optional Payment in one transaction | HTTP/API/Chromium/concurrent PostgreSQL paths tested; fixed last-stock replay, rounding and lock ordering. |
| Collect/reverse | Invoice detail → payments views/services → invoice lock → Payment/ledger → recomputed balance/status | Partial/split collections exist; fixed retry races, token conflicts and invalid reversals. This records settlements; it does not execute a bank/card payment. |
| Return | Return form → request_return → pending lines → manager decision → invoice/product locks → credit, restock or damaged write-off, ledger and refund atomically | Approval flow tested; fixed duplicate/fractional requests, zero-value cancellation and outstanding shipping. |
| Purchase | PO form → PO/lines → linked draft receipt → confirm → PO quantities, stock ledger and latest cost → supplier bill → payment | Core chain tested; fixed service/document validation. Cumulative invoice matching and supplier credits remain incomplete. |
| Count | Stock-take form → locked count batch → validate all → save/apply → stock corrections | Fixed partial saves, changes after application and intervening-movement overwrites. Conflicting counts are refused atomically; start a fresh count with movements paused. |
| Import | Upload → parse/validate → uploader-scoped ImportJob preview → one-time locked transactional apply → models/movements | Working supported imports; update quantities are additive and exports are not a lossless round trip. |
| Report | Permission/range validation → ORM aggregation → HTML/chart/CSV/XLSX/PDF | Arithmetic/query-budget tests exist. Original-sale margin is not net profit or return-adjusted profit. |
| Recover | Backup dump/media/snapshot/checksums → scratch DB/media restore → counts/totals/integrity comparison | Reproduced Linux failure from Windows CRLF, added LF checkout policy; synthetic records and product image restored successfully. |

Python symbols in the tables are under `apps/` unless a full path is shown.

## Feature audit

### Catalogue, suppliers and inventory

| Feature | Importance | Status / files or screens | Observed gap and business impact | Dependencies | Priority |
|---|---|---|---|---|---|
| Product CRUD/archive/restore | Essential | Verified: products/views.py, forms.py, product screens | Historical sales retained; archived stock still needs operational handling | Existing product permissions | Maintain |
| Categories/brands | Essential categories; useful brands | Partial: Product/Category/Brand, categories screen | Add/list exists; no dedicated rename/merge/delete flow | Reference/history rules | P2 |
| SKUs | Essential | Verified: Product constraints, model tests | Case-insensitive uniqueness; no automatic allocation | Owner numbering convention if needed | Maintain |
| Barcode search/scanning | Essential POS | Verified keyboard-wedge flow, fixed: ProductSearchView, invoice.js, new E2E tests | Exact scan could be displaced by name matches; scanner Enter before debounce and stale results could select wrong item. Fixed. Camera/labels absent | Physical device not tested | P0 fixed; camera P3 |
| Variants | Useful | Partial workaround: separate Product SKUs | No parent/attribute/variant management | Actual size/colour assortment | P2 |
| Units/fractional quantities | Essential whole units; conditional weighed goods | Partial: unit labels, integer models | “kg” is a label; 1.5 kg sales and unit conversion unsupported | Confirm weighed goods/precision | P1 if applicable |
| Prices/cost/overrides | Essential | Verified: ProductForm, create_invoice, roles/audit | Catalogue price enforced; permissioned overrides. No customer price lists/scheduled promotions | Pricing/promotion policy | P2 extensions |
| Suppliers | Essential | Verified basic CRUD/import/history; access fixed: suppliers/, supplier detail | Unauthorized supplier balances exposed through detail page; now hidden. Dedicated export missing | Supplier invoice read permission | P0 fixed; export P2 |
| Purchase orders | Essential replenishment | Verified core, validation fixed: purchasing/models/forms/views/services, order screens | Costs/discounts/state needed service validation; duplicate products made receipt mapping ambiguous. New orders use one line/product | Review old duplicate orders before receiving | P0 fixed |
| Goods receipt/partial receiving | Essential | Verified, relationships fixed: create_receipt/confirm_receipt, receipt screens | Wrong supplier/order/product links could fulfil wrong PO or misstate stock. Now validated; confirmation once-only | Valid PO lines/active supplier | P0 fixed |
| Supplier invoice matching | Essential basic payable; useful three-way matching | Partial: SupplierInvoice/Line, bill screen | Identity/confirmed receipt now checked. No cumulative billed-quantity match; same receipt can support duplicate bills; global number rejects some different-supplier references | Partial billing/tolerance/numbering policy | P1 |
| Supplier payments/dues | Essential | Verified recorded payments, retries fixed: pay_supplier_invoice, payables | Serialized token replays; unknown methods/conflicts rejected. No supplier credit note/payment reversal | Credit/reconciliation policy | P0 fixed; corrections P1 |
| Stock ledger/tracking | Essential | Verified service workflows: inventory/services/models, reconciliation | Locked balances/DB checks; immutability is application-level, not protection from arbitrary SQL | Restricted DB privileges | Maintain; DB enforcement P2 |
| Adjustments/damage | Essential | Verified supported forms: adjust_stock, movement screen | Reason/permission required; direct-service extreme inputs/type constraints not exhaustive | Approval thresholds if required | P2 |
| Stock-takes | Essential | Verified controlled-count workflow, fixed: save_stocktake_counts/apply_stocktake, stock-take screens | Batch saves all-or-nothing; applied counts immutable through UI. Now rejects snapshot mismatch or any later movement for counted products, rolling back every correction; prior live-count safety claim corrected | Pause movements/start fresh session after conflicts; live-count design if needed | P0 fixed; live counts P2 |
| Low stock/reorder | Essential | Verified on-screen: inventory filters/dashboard/low_stock report | Suggestion is twice threshold, not lead-time planning; no proactive notification delivery | Lead times/recipients | P2 |
| Transfers/reservations | Not applicable to current single pool; conditional useful | Missing: no location/reservation model | Cannot transfer locations or reserve orders | Locations/reservation expiry policy | P1 if applicable, else P3 |
| Batches/lots/serials | Optional; essential for relevant goods | Missing: product-level quantities only | Cannot trace a lot/unit, serial sale or recall | Actual product classes/traceability rules | P1 if applicable |
| Expiry | Useful; essential perishables | Partial: Product.expiry_date, inventory expiry filter | One date per product; no receipt-lot expiry/FEFO or expired-sale blocking | Lot data/expiry-sale policy | P1 if applicable |

### Checkout, customers and finance

| Feature | Importance | Status / files or screens | Observed gap and business impact | Dependencies | Priority |
|---|---|---|---|---|---|
| POS/cart/search/checkout | Essential | Verified: billing screen, sales service/API, browser tests | Server-authoritative prices, stock and totals; no offline operation | Available backend/database | Maintain |
| Held/resumed sales | Useful | Missing: no held cart model/storage | Navigation/reload loses unfinished cart | Cart ownership/privacy/retention; reservation decision | P2 |
| Invoice numbering/duplicates | Essential | Verified, last-stock retry fixed: DocumentSequence/create_invoice/race tests | Retry previously could fail stock check after successful original sale consumed last units | Caller must provide token for replay protection | P0 fixed |
| Discounts/decimal totals | Essential | Verified supported arithmetic; rounding fix: sales/calculations, core/money | Individual payments now rounded before credit checks; nonfinite money rejected. Browser floats remain preview only | Existing discount rules | P0 fixed |
| Tax configuration/snapshots | Conditional essential | Functional generic calculator; not compliance verified: tax screens, Product/Invoice/InvoiceLine | Preserved pending settings/defaults/bulk rates/snapshots. Return tax credits absent; purchase tax assumptions need separate review | Jurisdiction/registration/product treatment | P1 applicability blocker |
| Counter rounding/e-invoicing | Conditional essential | Missing: no rounding adjustment/MyInvois lifecycle | Cents rounding is not nearest-5-sen counter rounding; PDFs are not authority-validated e-invoices | Applicability, channels, official rules/credentials | P1 if applicable |
| Invoice/receipt print/PDF | Essential | Verified: invoice_print.html, sales/pdf.py, export/browser tests | Branded invoice and words exist; no dedicated thermal/fiscal format | Printer/authority specifications | P2 customization |
| Multiple/split/partial payments | Essential | Verified local recording; race fixed: payments/services/models, invoice screens | Cash/card/bank/UPI/other are classifications, not processor confirmation | Processor/terminal contract for integration | P0 fixed; integration P2 |
| Returns/refunds/credit notes | Essential | Verified supported flow; controls fixed: returns/, payments/ | Manager approval, cumulative quantities, damaged/resellable movements and payouts atomic. No return submission token; pending quantities prevent over-return | Refund method/approval | P0 fixed; replay UX P2 |
| Cancellation/reversal | Essential | Verified safeguards fixed: cancel_invoice/reverse_payment/admin regression | Blocks cancellation after approved zero-value return; prevents negative receipts, overpayment and changes to cancelled balances | Audited correction workflow | P0 fixed |
| Returned sale with shipping | Essential supported rule | Verified fixed: recalculate_invoice/InvoiceDetailView/HTTP test | Outstanding nonrefundable shipping now has truthful status and can be collected | Existing shipping rule preserved | P0 fixed |
| Exchanges | Useful | Partial workaround: return + independent new sale | No linked exchange or difference settlement | Exchange pricing/document policy | P2 |
| Customer profiles/history | Essential | Verified: customers/, detail/invoice screens, API | Detail shows last 20 invoices; no dedicated paginated history | Invoice read permission | P2 UX |
| Customer balances/credit limits | Essential credit trade | Verified, permission fixed: outstanding_for/CustomerForm/create_invoice | Cashier could change limits via HTML despite API protection. Now permissioned; checkout rereads customer under lock | Manager authority | P0 fixed |
| Customer ledger | Essential credit trade | Verified supported postings: LedgerEntry/post_ledger_entry/ledger screen | Manual entries do not allocate invoices; ledger and invoice-based outstanding can differ | Allocation/write-off policy | P1 explain/reconcile |
| Loyalty | Optional | Missing: no points/tier/redemption module | No program assumed | Earn/redeem/expiry policy | P3 |
| Store credit/advances | Useful | Missing: overpayment blocked | No credits against future sales | Liability/refund/expiry/accounting policy | P2 |
| Expenses | Essential for in-app operating profit; otherwise useful | Missing: no model/screens | Margin cannot be used as net profit | Categories, approval, external accounting ownership | P1 decision |
| Register open/close/daily cash reconciliation | Essential staffed cash POS | Missing: daily collections only | No opening float/drawer/session, paid-in/out, counted cash, variance approval or close lock | Drawer/shift/variance policy | P1 |

### Reports, security, settings and deployment

| Feature | Importance | Status / files or screens | Observed gap and business impact | Dependencies | Priority |
|---|---|---|---|---|---|
| Dashboard | Useful | Verified role-aware display; partial data restrictions: DashboardView/dashboard.html | Shared query computes sales/customer figures even for some inventory roles | Define granular financial visibility | P2 |
| Sales/purchase/inventory reports | Essential | Verified defined reports: reports/queries/views, accuracy/query tests | Dimension reports use original sales; current balance reports are not historical as-of snapshots | Agreed metric definitions | P1 definitions; P2 extensions |
| Profit | Useful | Partial estimate: sales_by/profit-estimate | Latest-cost legacy estimates; returns/damaged cost/expenses absent. Added explicit original-sale limitation | Costing/expense policy | P1 if used for accounting |
| Tax reports | Conditional essential | Partial: tax_summary/tax screens | Original invoice tax only; no credit-note tax ledger/filing output | Verified return/tax rules | P1 if filing from app |
| Staff/RBAC | Essential | Verified core role matrix; bypass fixes: accounts, AppPermissionMixin, API perms | Six roles; self-lockout guard. No branch-specific authorization | Single-business assumption | P0 fixes; extensions conditional |
| Financial admin safety | Essential | Verified fixed: HistoricalDocumentAdmin, transaction admin registrations | Generic admin could change/delete financial documents outside ledger/stock services. Now inspection-only even for superusers | Use business correction workflows | P0 fixed |
| Approvals | Essential returns; useful elsewhere | Partial: ReturnDecisionView | No discount threshold/purchase/adjustment approval state machine; PO status changes lack dedicated audit event | Owner thresholds/separation of duties | P2 |
| Audit log | Essential | Verified application-level: audit/ | Sensitive action history, not DB-level tamper resistance/hash chain/remote retention | Privileged access/retention policy | P2 |
| Authentication/session/reset | Essential | Verified local controls; SMTP configured only: accounts/settings/axes | Lockout/session/CSRF tested; actual mail delivery untested. API tokens do not expire; token issuance needs explicit throttling review | SMTP/token lifecycle | P1 before public API use |
| API/integrations | Useful | Verified limited API: api/ | Catalogue/stock reads, customers create/read, invoices create/read; no full purchase/return API, gateway, webhook/outbox | Integration contract | P2 |
| Tenant/store isolation | Not applicable single business; essential SaaS/multi-store | Missing: no tenant/store FK | Authorized users see all business records; do not market as isolated multi-tenant software | Architecture and migration/backfill | P0 before such use |
| Export/data visibility | Essential | Payment CSV leak fixed, broader policy partial: PaymentExportView/invoice/API | Refund CSV respects view_refund. Embedded refund history in invoice detail/print/API remains visible to invoice readers; owner must define intended policy | Visibility policy | P1 review |
| Store settings/receipt customization | Essential basics; useful editor | Partial: settings.BUSINESS/tax settings/templates | Company/timezone/currency/notes mainly env/template edits; words formatter assumes Ringgit/Sen; no general receipt editor | Actual currency/branding/printer | P2 |
| Import/export | Essential basic migration; useful operations | Verified supported files, partial round trip: core.imports/importers/export views | Additive stock on update; export stock header/metadata not round-trip safe. Supplier export absent. XLSX materializes rows before limit check; barcode/stale preview failures need friendlier handling | File budgets/add-vs-replace semantics | P1 hardening; P2 round trip |
| Backup/recovery | Essential | Actual synthetic round trip verified; LF fix: scripts/*.sh/.gitattributes/data_snapshot | Compose does not schedule backups. Dump and manifest do not share a snapshot; pause writes during verification backup. Encryption/off-host and RPO/RTO unconfigured | Schedule/keys/retention/recovery targets | P0 LF fixed; P1 operations |
| Production artifact/config | Essential | Build/checks verified; real TLS unverified: Dockerfile/Compose/Nginx/Gunicorn | Local HTTP != production TLS. Bound worker count for 768 MB; default follows host CPU. Proxy/IP trust, mail and monitoring need setup | Host/TLS/resources/SMTP | P1 launch preparation |
| Release pipeline | Essential if used | Revision mismatch fixed; YAML parsed: .github/workflows/ci.yml/deploy.yml | CI could test another ref. Now resolves one SHA and tests/builds/publishes that SHA; no remote run/publication performed | GitHub reviewers/registry permissions | P0 fixed; remote run pending |
| Performance/indexes | Essential | Existing query-budget tests pass; no new load claim: indexes/test_query_counts/Locust | Pagination/prefetch/indexes exist; icontains scans large catalogues; return summary repeats line queries | Real volume/EXPLAIN/load profile | P2 |
| Responsive/accessibility | Essential | Browser smoke verified, partial accessibility review: app.css/base.html/mobile test | 375px smoke, labels/focus/skip link/reduced motion; no full WCAG/assistive audit; autocomplete active-descendant semantics incomplete | Devices/assistive tests | P2 |

## Implemented stages

1. **Financial integrity:** moved payment/refund/supplier token checks under invoice locks; bound tokens to operations; handled cross-invoice token conflicts safely; standardized reversal lock order and customer-before-receipt-sequence locking; blocked invalid reversals; fixed returned shipping, zero-value cancellation, last-stock replay, rounded split payments and nonfinite amounts.
2. **Authorization and validation:** restricted credit-limit edits, supplier balance exposure, combined receive/bill shortcut, refund CSV and financial admin mutations; added purchase quantity/cost/discount/rate/state/supplier/document-link checks; reread confirmed receipts from DB; rejected duplicate/fractional returns; saved stock-take batches atomically.
3. **Checkout and operation:** exact barcode/SKU priority, immediate scanner Enter, obsolete response rejection, visible search failures; immutable release SHA; LF checkout rule after reproducing script failure; honest original-sales report note; tests and formatting.

Existing tax work was preserved and formatted without changing its rates/policy. Deterministic demo randomness is now explicitly marked as non-security use for Bandit.

No dependencies added/removed. No schema migration introduced by this review. No live data, `.env`, credentials, Git identity, GitHub authentication, staging/index, commits or remote branches changed.

## Practical roadmap and unanswered business questions

### Critical fixes — implemented; release/rehearsal required

Review the combined diff, run CI on the intended SHA, rehearse pending tax migrations on a restored copy, investigate inconsistent existing records and take a verified pre-release backup. The controlled-count workflow now enforces no intervening movements; pause movements and restart counts after conflicts. Do not use this single-business application for multi-tenant access.

### Essential completion — next stage

1. **Register close:** agree opening float, drawer ownership, tender scope, paid-in/out, reversal dates, blind counts, variance approval and reopening; then extend payments with immutable close snapshots and concurrent-close tests.
2. **Supplier corrections/matching:** agree partial billing/tolerances/reference scope; match billed quantities against confirmed receipt lines, add credits/reversals, and reconcile old links before any backfill.
3. **Counting operations:** adopt the enforced paused-movement workflow. If live counts are needed, design an explicit movement cutoff/recount process; subtracting from live stock alone does not preserve sales since the physical count.
4. **Compliance applicability:** confirm jurisdiction, registrations, turnover/group circumstances, products and counter channels. Only then implement stored rounding/tax-credit adjustments and authority submission IDs/statuses with deterministic tests.
5. **Operations:** bounded workers, TLS/cookies/proxy trust, SMTP, explicit token throttling/lifecycle, scheduled/alerted/off-host encrypted backups, recovery objectives and stricter XLSX/import budgets.
6. **Financial definitions:** decide ownership of expenses/external accounting, manual-ledger allocation, return-adjusted margins and historical reports before changing accounting outputs.

### Useful/later improvements

Held carts without implicit reservation; lossless catalogue exports; receipt/thermal templates; large-PO entry; history pagination; barcode labels; measured query optimization; accessibility tests; configurable alerts; variants and authorized integrations. Loyalty/camera/offline are optional. Warehouses/transfers, lots/FEFO, serials and fractional stock require confirmed business need, stock-schema design and reviewed backfills.

Specific questions still outstanding:

- Is Malaysia the actual jurisdiction? Which registrations/product tax rules apply, what are turnover/group circumstances, and which transactions are over-the-counter?
- Are goods weighed, perishable, batch/serial tracked, or stored at multiple locations?
- Who owns/closes drawers and approves variances/reopening? Are reversals attributed to action date or original payment date?
- Can supplier invoices split receipts, what tolerances apply, and are references unique per supplier?
- Should invoice readers see embedded refund histories, or should view_refund restrict all those surfaces?
- Are expenses/customer credit liabilities maintained here or externally? What are the write-off/exchange/store-credit policies?
- What are the production domain/TLS setup, SMTP/provider, payment processor, backup destination/retention and recovery objectives?

Independent fixes proceeded without waiting for these answers.

## Jurisdiction evidence and limitations

The Malaysian inference comes from `config/settings.py`, not the computer's timezone. Official sources checked on 21 November 2026:

- [HASiL e-Invoice timeline](https://www.hasil.gov.my/en/e-invois/pelaksanaan-e-invois-di-malaysia/garis-masa-pelaksanaan-e-invois/) was updated 30 August 2026 and lists implementation phases and an exemption below RM3 million. Actual owner applicability/detailed exceptions are unestablished; the app has no MyInvois validation/submission integration.
- [Bank Negara Malaysia counter rounding](https://www.bnm.gov.my/-/introduction-of-a-rounding-mechanism-for-over-the-counter-payments) describes nearest-5-sen rounding on the final tax-inclusive bill, not each item. The app rounds cents only. No change was made without confirming channels and the stored financial adjustment design.

These checks identify gaps, not compliance. Configurable GST labels, PDFs and arithmetic tests are not evidence of legal compliance. No historical tax information was inferred/backfilled.

## Database and deployment preparation

Already pending before this review: `apps/products/migrations/0002_taxconfiguration.py` and `apps/sales/migrations/0002_invoice_tax_label_invoice_tax_registration_number.py`. They add singleton tax settings and invoice label/registration snapshots. This review adds no migration; fresh migration and model consistency passed in isolation.

Before production: backup/verify, rehearse `python manage.py migrate --noinput` and `python manage.py setup_roles`, review existing receipt/supplier links, refunds and reconciliations. Historical default tax labels cannot recover past registrations automatically; any correction needs source evidence and a separately reviewed/audited backfill. No automatic financial/stock repair was run.

Deploy a specific tested image. Release images now use SHA tags and lowercase registry paths (required for the mixed-case repository name); host rollout must consume the same tag. Compose currently builds locally; it does not automatically switch to GHCR images. Real TLS, SMTP/processors, scheduled backups and remote Actions execution remain deployment work. With SSL redirect enabled, also verify the health probe actually reaches the database check rather than accepting an HTTP redirect. No production deployment/publication was performed.

Existing stored invoice statuses are not automatically backfilled; the corrected calculation runs on later financial mutations. Review old fully-returned invoices with unpaid shipping before an explicitly audited status recalculation. Return completion now follows approved quantities, so fully crediting paid goods does not hide remaining free goods. Fully returning free goods also preserves an outstanding shipping balance. Historical pending returns with duplicated sale lines must be rejected and resubmitted correctly; approval now refuses them.

## Verification results

| Check | Actual result |
|---|---|
| Full suite: `pytest -n 4 --cov=apps --cov-report=term --cov-report=json:/tmp/retail-audit-coverage.json -q --tb=short` | **294 passed**, 170.49 seconds; unit/integration/API/security/concurrency/legacy characterization and real Chromium E2E included; no skips in final run |
| Combined statement/branch coverage | **89%** (4,608 statements; 982 branches; management commands/migrations excluded by existing configuration) |
| New regression coverage | 38 integration cases plus 2 Chromium cases; includes admin, money/credit/bulk error handling, zero-price/quantity-based returns, cancellation, purchasing links, count atomicity/movement conflicts, exact scans, stale search and four concurrent retry scenarios. Targeted integrity/HTTP suite also passed before the last bulk-price case: 83 tests |
| Ruff lint / format | Passed; 169 files already formatted in final repository check |
| Bandit | Passed, no findings; existing nosec-comment parser warnings remain |
| `pip-audit -r requirements/base.txt --strict` | No known vulnerabilities reported on this run; not a future guarantee |
| Django system check / migration consistency | Passed / no model changes detected |
| Production `check --deploy --fail-level WARNING` | Passed with isolated production-like environment; existing deliberate HSTS preload warning silenced |
| Fresh migrations and role setup | Passed on disposable PostgreSQL 16 |
| Production image | `retail-audit:20261121` built, including manifest static collection; runtime smoke passed: UID 10001, read-only filesystem, healthy DB-backed probe and HTTP 200 login; LF recovery scripts confirmed inside image; no push |
| Backup/restore | Checksums, PostgreSQL restore, record counts/totals, stock/invoice/orphan checks and synthetic product-image presence passed |
| Compose / workflow YAML | Configuration validated; workflow YAML parsed; remote Actions execution not performed |
| Static typing / frontend build | Not configured / no Node frontend build; production collectstatic exercised |
| Full load/DAST/physical hardware | Not run; query budgets and synthetic concurrency are not production load certification; physical scanner/printer/terminal not tested |

Initial security subprocess checks failed because the Windows checkout's `.env` supplied HTTP development cookie settings and a secret when the test removed its environment value. Final tests used a source copy excluding `.env`/credentials/data. An intermediate purchasing test exposed a stale receipt object in the new validation; authoritative DB reread fixed it. Linux recovery initially failed on CRLF; normalized scripts plus `.gitattributes` fixed that. Final results above are after these corrections.

## File accounting and Git review

Created for this review: `.gitattributes`, `apps/core/admin.py`, `tests/integration/test_integrity_controls.py`, `tests/e2e/test_checkout_integrity.py`, this report and evidence snapshots under `docs/evidence/`.

Modified implementation files: payment services/views; sales services/views/admin; purchasing services/views/admin; returns services/admin; inventory services/views; customer forms/views/admin; supplier views; product lookup; reports/queries; static/js/invoice.js; inventory/product/supplier templates; CI/deploy workflows; existing E2E retail workflow; seed_demo's Bandit annotation. Updated docs/BUSINESS_RULES.md and docs/SECURITY.md to correct earlier safety claims and link this review. Recovery scripts were normalized to LF; Git may show no text diff because their committed content was already LF.

Pre-existing changes retained (some formatted): `.env.example`, README.md, accounts/roles.py, core/context_processors.py, seed_demo.py, products/forms.py/importers.py/models.py and new tax modules/migration, sales/calculations.py/models.py/pdf.py/services.py and tax snapshot migration, config/urls.py, docker-compose.yml, invoice.js, base/sales/tax templates, test_calculations.py and tax tests, scripts/start-local.ps1. Shared files contain both kinds of work. The final status/diff includes both; do not attribute the complete diff to this review.

The complete final inventory/status and diff snapshots are supplied in `docs/evidence/retail-review-status.txt` and `docs/evidence/retail-review.diff`. The diff snapshot excludes its own generated contents. Disposable test/runtime containers and their synthetic data were removed after verification; the locally built image remains available. The original application stack/data were not started, stopped or migrated.

Manual commands, after reviewing **both** this work and pre-existing changes (none executed by the assistant):

```powershell
git status --short
git diff --stat
git diff
# Stages the whole reviewed working tree, including pre-existing tax/startup work.
git add --all
git diff --cached --stat
git diff --cached --check
git commit -m "Harden retail transaction integrity and operational controls"
git push origin claude/jolly-albattani-lso27a
```

To split commits, use interactive `git add -p` and explicit new-file paths instead. Include the pending tax modules/templates/migrations with their dependent changes. No staging/commit/push/authentication commands were executed during this task.
