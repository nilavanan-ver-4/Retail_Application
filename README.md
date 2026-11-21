# Retail Application (Django)

Retail management for **ASKER ENTERPRISE**: products & inventory, customers, suppliers, purchasing, sales
& invoicing (PDF), payments, returns, reports, audit trail and a REST API — migrated from the original Flask
app, with the legacy data and business rules preserved.

| | |
|---|---|
| Framework | Django 5.2 LTS, Django REST Framework, Python 3.13 |
| Database | PostgreSQL 16 (psycopg 3) |
| PDF | WeasyPrint · Frontend: Django templates, plain CSS/JS (no build step, strict CSP) |
| Tests | pytest + pytest-django, Playwright (browser), Locust (load) |

> The original Flask application is kept, untouched, in [`legacy_flask/`](legacy_flask/) as a controlled
> rollback option. See [docs/MIGRATION.md](docs/MIGRATION.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/RETAIL_REVIEW_2026-11-21.md](docs/RETAIL_REVIEW_2026-11-21.md) | Current end-to-end feature audit, integrity fixes, 294-test verification, remaining gaps and implementation roadmap |
| [docs/AUDIT.md](docs/AUDIT.md) | Feature inventory of the Flask app, route mapping, defects & vulnerabilities found |
| [docs/MIGRATION.md](docs/MIGRATION.md) | Data-migration strategy, schema mapping, checklist, assumptions, intentional behaviour changes, rollback |
| [docs/BUSINESS_RULES.md](docs/BUSINESS_RULES.md) | Money, tax, stock, payment, return and report definitions |
| [docs/FEATURE_COMPARISON.md](docs/FEATURE_COMPARISON.md) | Existing vs new features, and what is deliberately deferred |
| [docs/SECURITY.md](docs/SECURITY.md) | Security review: findings, severity, evidence, remediation, retest |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Setup, backup/restore, deployment, rollback, troubleshooting |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | What was actually run, with real pass/fail counts and performance numbers |

## Project layout

```
config/            settings (env-driven), urls, wsgi/asgi
apps/
  core/            money helpers, document sequences, import engine, legacy importer, security middleware
  accounts/        custom user, roles (Administrator, Manager, Cashier, Inventory Staff, Purchasing Staff, Reporting)
  audit/           append-only audit log
  products/        catalogue, categories/brands, CSV/Excel import & export, bulk update
  inventory/       stock ledger (immutable movements), adjustments, stock-takes, reconciliation, FIFO ageing
  customers/       customers, account ledger, credit limits
  suppliers/       suppliers
  purchasing/      purchase orders → goods receipts (stock in) → supplier invoices → supplier payments
  sales/           invoices, Decimal calculations, billing screen, printable HTML + PDF
  payments/        payments (split/partial), reversals, refunds, idempotency
  returns/         return requests, manager approval, resellable/damaged disposition, credit notes
  reports/         19 reports (CSV / Excel / PDF), dashboard queries
  api/             DRF API (products, customers, invoices, stock movements) + OpenAPI schema
templates/ static/ tests/ scripts/ docker/ docs/ legacy_flask/ samples/
```

## Quick start (development)

For a local Docker installation on Windows with Docker Desktop running:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start-local.ps1
```

On first run this creates a git-ignored `.env` with random secrets, binds the app to
http://127.0.0.1:8000, builds the image, waits for PostgreSQL, applies migrations,
sets up roles and the first administrator, then starts and checks the stack.
Find the login credentials in `.env` under `BOOTSTRAP_ADMIN_USERNAME` and
`BOOTSTRAP_ADMIN_PASSWORD`. Existing `.env` files are preserved; ensure they contain
the required database and bootstrap settings. Use `-Port 8080` on first run to choose
another port. Local HTTP uses secure-cookie flags set to `false`; use `true` with HTTPS
in production. The production Compose defaults remain port 80 on all interfaces.

```powershell
docker compose logs --tail 100 web nginx   # inspect logs
docker compose stop                      # stop; keep database and media
docker compose up -d --wait               # restart
```

Database and uploaded media persist in Docker named volumes. Demo data is optional;
the startup workflow creates an empty business database.

Prerequisites: Python 3.13, PostgreSQL 14+ (16 recommended), `libpango` for PDFs (`apt install libpango-1.0-0 libpangoft2-1.0-0`).

```bash
git clone https://github.com/nilavanan-ver-4/Retail_Application && cd Retail_Application
python -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt

# 1. database
sudo -u postgres psql -c "CREATE ROLE retail LOGIN PASSWORD 'choose-a-password' CREATEDB;" \
                      -c "CREATE DATABASE retail OWNER retail;"

# 2. configuration (the .env file is git-ignored — never commit it)
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"   # paste into SECRET_KEY
$EDITOR .env                                                    # set DATABASE_URL, DEBUG=true for local dev

# 3. schema, roles, first administrator
python manage.py migrate
python manage.py setup_roles
BOOTSTRAP_ADMIN_USERNAME=admin BOOTSTRAP_ADMIN_PASSWORD='a-long-unique-password' python manage.py bootstrap_admin

# 4. run
python manage.py runserver            # http://127.0.0.1:8000
```

Optional demo data (synthetic — never run on a real database):
`python manage.py seed_demo --password 'Demo-Passw0rd-2026!' --products 200 --customers 100 --invoices 300`

### Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `SECRET_KEY` | Django secret. **Required** unless `DEBUG=true` (no insecure fallback) | – |
| `DEBUG` | Debug mode — keep `false` in production | `false` |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Comma-separated hosts / origins | empty |
| `DATABASE_URL` | `postgres://user:pass@host:5432/db` (**required**) | – |
| `LEGACY_DATABASE_URL` | Old Flask database, used only by `import_legacy` (opened read-only) | – |
| `TIME_ZONE` | Business time zone; also used to read legacy naive timestamps | `Asia/Kuala_Lumpur` |
| `SECURE_SSL_REDIRECT`, `TRUST_PROXY_SSL_HEADER`, `SECURE_HSTS_SECONDS` | HTTPS hardening (active when `DEBUG=false`) | off / off / 1 year |
| `SESSION_COOKIE_AGE`, `AXES_FAILURE_LIMIT` | Session length (sliding), failed logins before lock-out | 8 h, 5 |
| `MEDIA_ROOT`, `MAX_UPLOAD_MB` | Uploaded product images, upload limit | `./media`, 5 |
| `EMAIL_*`, `DEFAULT_FROM_EMAIL` | SMTP for password-reset e-mails (console backend by default) | console |
| `COMPANY_*`, `CURRENCY_SYMBOL`, `TAX_LABEL`, `TAX_DEFAULT_RATE` | Invoice branding and tax label | ASKER ENTERPRISE, RM, GST, 0 |

## Migrating the existing Flask data

### Managing invoice tax

Open **Tax** in the sidebar. Administrators and managers can set a tax label,
registration number and the default rate for newly created products. Select existing
products, enter a rate and a reason, then choose **Apply rate to selected products**.
Use 0% for untaxed products. Prices exclude tax; tax is added after line and invoice
discounts. Billing shows each line's rate, tax and total before saving.

Issued invoices save their tax rates, label and registration number, so later settings
changes do not rewrite invoice history. The invoice tax summary offers date filters
and CSV export; it excludes cancelled invoices and shows original amounts before
return credits or refunds. Demo seeding now defaults to 0% tax; use `seed_demo
--tax-rate 6 ...` only when an explicit sample rate is wanted. Older demo records
retain the sample rates they were created with.

```bash
export LEGACY_DATABASE_URL=postgres://readonly_user:pw@host:5432/postgres   # the old database
python manage.py migrate && python manage.py setup_roles
python manage.py import_legacy --dry-run --report /tmp/report.json           # validates, loads, rolls back
python manage.py import_legacy --report legacy_import_report.json            # real import
```

Read [docs/MIGRATION.md](docs/MIGRATION.md) **before** running this on production data (backup first, rehearse
on a staging copy, review the anomaly report).

## Tests

```bash
pytest -n 4                            # unit + integration + security + API + characterization (no browser)
pytest tests/e2e -m e2e                # Playwright (needs: playwright install chromium)
pytest --cov=apps --cov-report=term-missing
ruff check . && ruff format --check . && bandit -q -r apps config -c pyproject.toml
pip-audit -r requirements/base.txt --strict
python manage.py check --deploy        # run with production env vars
locust -f tests/perf/locustfile.py --headless -u 25 -r 5 -t 90s --host http://127.0.0.1:8000
```

The characterization tests (`tests/characterization`) run the **original, unmodified Flask code** against a
database built from `legacy_flask/DB/*.sql` (needs `pip install -r requirements/legacy.txt`; they skip otherwise).

## Production

`docker-compose.yml` runs Nginx → Gunicorn/Django (non-root, read-only filesystem) → PostgreSQL (no published
port). Backups: `scripts/backup.sh`, verified with `scripts/verify_restore.sh`. Full procedures, rollback and
troubleshooting: [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Roles

| Role | Can |
|---|---|
| Administrator | Everything, plus user management |
| Manager | All business operations incl. cancel invoices, approve returns, price override, adjust stock, audit log |
| Cashier | Sell, take payments, raise returns, manage customers; **no** cost prices |
| Inventory Staff | Products, stock adjustments, stock-takes, receive goods, inventory reports |
| Purchasing Staff | Suppliers, purchase orders, receipts, supplier invoices & payments |
| Reporting | Read-only access to sales, inventory and financial reports |

All permissions are enforced on the server for every request (HTML views and API).

## License

See [LICENSE](LICENSE).
