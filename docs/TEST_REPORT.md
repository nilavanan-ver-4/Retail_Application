# Test report (what was actually executed)

Environment: sandbox VM, 4 vCPU / 15 GB, Python 3.13.17, PostgreSQL 16.15 (local), Chromium (Playwright 1.64.1).
Dates: 2026-10-26 … 10-28. Nothing below is estimated; `docs/evidence/` holds the raw load-test CSV.

## 1. Automated suite — `pytest -n 4 --cov=apps`

**240 passed, 0 failed, 0 skipped** (≈ 50 s) · branch coverage **89 %** of `apps/` (4,322 statements).

| Group | Tests | Result |
|---|---|---|
| Unit — Decimal invoice maths, rounding, refunds, amount-in-words, legacy availability scale, DB constraints | 40 | ✔ |
| Integration — invoice/stock/payment/return services (incl. **concurrent** oversell & duplicate submission) | 43 | ✔ |
| Integration — HTTP workflows (billing tampering, imports, purchasing, stock-take, bulk update, uploads) | 46 | ✔ |
| Integration — access-control matrix (6 roles × 21 screens + escalation, lock-out, sessions) | 34 | ✔ |
| Integration — security regressions (SQLi, XSS, CSRF, CSP, redirects, reset, SSRF, audit immutability, `check --deploy`) | 18 | ✔ |
| Integration — REST API | 14 | ✔ |
| Integration — report accuracy (hand-computed totals) / page rendering / query counts | 20 / 5 / 2 | ✔ |
| **Characterization** — original Flask code run on a real legacy DB; Django parity; importer on its output | 9 | ✔ |
| **Browser E2E** (Chromium) — login→product→receive→sale→PDF→payment→return→reports; validation; oversell; double-click; mobile/tablet/desktop; print; 403; confirm dialogs | 9 | ✔ |

Quality gates: `ruff check` ✔ · `ruff format --check` ✔ · `bandit` 0 issues (6 documented `mark_safe` suppressions in the SVG helper, escape-tested) · `makemigrations --check` ✔ · `manage.py check` ✔ · `check --deploy --fail-level WARNING` ✔ (production env) · `pip-audit --strict` ✔ no known vulnerabilities.

Defects found *by these tests* and fixed during the build: chart-label XSS (D-01), bulk-update audit crash, un-normalised
audit values, Inventory-role gap, mobile overflow, report row ordering (see SECURITY.md).

## 2. Migration & data-preservation checks (real PostgreSQL)

* Legacy DB built from `legacy_flask/DB/table.sql + trg.sql`; original `app.py` driven through 12 operations (supplier, 2 products, 4 invoices incl. oversell attempt and forged totals, goods receipt, ledger entry, 11 page loads).
* `import_legacy`: suppliers 2→2, products 2→2, customers 5→5, invoices 4→4, lines 5→5, payments 4→4, ledger 6→6, receipts 1→1, supplier invoices 1→1; Σ invoice total `7368.70`=`7368.70`; Σ completed payments `437.40`=`437.40`; stock units `221`=`221`; stock value at cost `9010.00`=`9010.00`; verification mismatches `[]`. PKs preserved; sequences advanced (new rows don't collide); legacy DB unchanged (re-queried); dry-run rolls back; non-empty target refused; read-only connection enforced.
* Anomalies detected and reported (not hidden): forged invoice (`lines_do_not_sum`, `overpaid_invoice`).
* Backup/restore (on the migrated data): verified restore ✔; corrupted dump ✘ detected; tampered manifest ✘ detected; restore into non-empty DB refused; DR restore == manifest.

## 3. Performance (Locust, production-mode Gunicorn 4 workers × 2 threads, 2,000 products / 500 customers / ≈4,000 invoices)

25 concurrent users, 5 users/s ramp, 90 s, load generator + Gunicorn + PostgreSQL **on the same 4-vCPU host**
(so results are conservative for CPU but not representative of your hardware — rerun on the target).

| Metric | Result |
|---|---|
| Requests / failures | **2,645 / 0 (0 %)** |
| Throughput | 29.4 req/s |
| Aggregate latency | p50 74 ms · p90 490 ms · p95 720 ms · p99 1.2 s · max 2.35 s |
| Product search (JSON) | p50 28 ms · p95 400 ms |
| Create invoice (POST, with stock lock, ledger, payment) | p50 88 ms · p95 580 ms · p99 960 ms |
| Billing screen · invoice detail · dashboard | p95 370 / 500 / 770 ms |
| Invoice PDF (WeasyPrint) | p50 390 ms · p95 1.6 s |
| Reports (sales summary / by product / valuation / receivables) | p95 560 ms / 1.2 s / 1.0 s / 980 ms |
| Login (PBKDF2, deliberate) | p50 520 ms |
| Memory | 424 → 589 MB total RSS for 4 workers, flat over the last 40 s |
| DB connections | 8 steady (persistent, `CONN_MAX_AGE=60`) |

Proposed thresholds for this hardware (all met): error rate 0 %; interactive pages p95 < 1 s; PDF/report p95 < 2 s; sale creation p95 < 1 s.
Query counts: no page's query count grows with row count (N+1 guard test, 18 pages), ceiling ≤ 40 queries/page.
Not done: caching (not justified by the measurements), pre-generated PDFs (≈ 0.4 s each, acceptable), soak > 90 s, multi-host load.

## 4. Dynamic security probes (production-mode server, synthetic data)

Traversal (3 variants) 404 · forged Host 400 · API without login 403 · schema/admin 302 · `Server: gunicorn` only ·
no stack traces · brute force: attempts 1-4 → 200, attempt 5+ → **429**, correct password during lock-out → 429.

## 5. Not executed / blocked

| Item | Why |
|---|---|
| `docker build` / `docker compose up` / image health-check | No Docker daemon in the sandbox. `docker compose config` and workflow YAML were validated; the Dockerfile has **not** been built here |
| GitHub Actions run | Workflows are written but cannot run until pushed; every step they contain was run locally except the Docker job |
| ZAP/Burp-class DAST, Trivy image scan | Tools unavailable |
| Zoho/Notion connectors, payment gateway | Not part of the codebase; no credentials |
| Test against the *real* production database | None provided — all data is synthetic or derived from the repository's SQL |
| Swagger UI under CSP in a browser | Not exercised |
