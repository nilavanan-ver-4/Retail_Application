# Security review

Current follow-up: [21 November 2026 retail review](RETAIL_REVIEW_2026-11-21.md) documents additional authorization, admin-mutation and financial-integrity findings and fixes, current verification, and open risks. The earlier blanket statement below that no high findings remain is historical, not a current certification.

Method: OWASP Top 10 / ASVS-style review of the code, automated tests (`tests/integration/test_security.py`,
`test_access_control.py`, `test_api.py`), static analysis (ruff `S` rules + Bandit), `pip-audit`, Django
`check --deploy`, and manual dynamic probes against a production-mode Gunicorn instance holding **synthetic data
only**. Nothing was run against a real or production system. Automated results are evidence, not proof:
this is **not** a claim that the system is completely secure (see "Not covered").

## Findings in the new code, and their treatment

| ID | Sev. | Finding | Evidence | Fix | Retest |
|---|---|---|---|---|---|
| D-01 | **Medium** | Stored XSS: database-derived labels (customer/product names) were written unescaped into server-rendered SVG chart markup | Ruff S308 review → `bar_chart` | Labels/titles escaped after truncation | `test_chart_labels_from_database_are_escaped` ✔ |
| D-02 | Low | Missing `SECRET_KEY` did not fail at start-up outside DEBUG | `test_missing_secret_key_fails_closed_in_production` | `ImproperlyConfigured` at import | ✔ |
| D-03 | Low | `check --deploy` W021 (HSTS preload) | CI | Preload is irreversible → explicit opt-in; check silenced with justification | ✔ |
| D-04 | Low | Bulk-update audit call crashed (kwarg clash) — an *audit gap*, not a bypass | `test_bulk_update_atomic_and_audited` | renamed field | ✔ |
| D-05 | Low | Price-change audit stored un-normalised decimals | `test_product_crud_archive_and_price_audit` | values re-read from DB | ✔ |
| D-06 | Info | Role gap: Inventory Staff could not receive goods (functional, found by E2E) | E2E | separation-of-duties permissions added | ✔ |
| D-07 | Info | Mobile overflow / unusable top bar at 375 px (UX) | E2E | CSS | ✔ |

No critical or high findings remain open in the Django code. All legacy findings (L-01…L-10 in AUDIT.md) are
resolved by the migration **except**: *rotate the old DB password and Flask secret (L-02) — they are in Git history.*

## Control verification

| Risk | Control | Verified by |
|---|---|---|
| SQL injection | ORM everywhere; one raw-SQL read in the importer/snapshot uses constants | payload tests on 7 list/lookup endpoints; Bandit/ruff clean |
| XSS | auto-escaping; **CSP `script-src 'self'`**, no inline scripts/handlers (asserted over all templates); JS uses `textContent` | tests ✔ |
| CSRF | Django middleware on all POSTs, logout is POST-only | `test_csrf_is_enforced…` ✔ |
| Broken access control / IDOR | permission per view and per API model-permission (incl. *view*); import jobs scoped to uploader; 33-case role matrix; API reads need view perms | ✔ |
| Privilege escalation | self role/deactivation edit blocked; role changes audited; API serializers read-only for limits/flags | ✔ |
| Price/total/stock manipulation | server recomputes; DB CHECK constraints; row locks; idempotency tokens | tampering tests (HTTP + API) ✔ |
| Authentication | PBKDF2, 10-char policy, axes lock-out (5 fails ⇒ HTTP 429, tested live), 1 h single-use reset tokens, no user enumeration, sliding 8 h session, cookies HttpOnly/SameSite=Lax (+Secure in prod) | ✔ |
| File upload | images: ≤2 MB, Pillow-decoded, JPEG/PNG/WebP only, Django-sanitised names, stored under `MEDIA_ROOT` and served by Nginx with `nosniff` + sandbox CSP; spreadsheets: parsed in memory, never stored | ✔ |
| Path traversal | whitenoise/Django static+media; probes `/static/../manage.py`, `%2e%2e`, `/media/../../etc/passwd` → 404 | ✔ live |
| SSRF / local file read via PDF | WeasyPrint fetcher allows `data:` only | `test_pdf_renderer_refuses_external_and_file_urls` ✔ |
| CSV/Excel injection | exported cells starting `= + - @` are prefixed | ✔ |
| Host header | `ALLOWED_HOSTS` → HTTP 400 | ✔ live |
| Open redirect | Django `next` validation | ✔ |
| Sensitive data | audit log never stores passwords (tested); logs omit request bodies; card numbers never stored; errors don't leak internals; `Cache-Control: no-store` when authenticated | ✔ |
| Deserialisation / command injection | no `pickle`/`eval`/`shell=True` in application code (Bandit) | ✔ |
| Transport / headers | HSTS, secure cookies, `SECURE_SSL_REDIRECT`, proxy header trust — all env-controlled; X-Frame-Options DENY, Referrer-Policy, Permissions-Policy | `check --deploy` clean ✔ |
| Dependencies | all pinned; `pip-audit --strict`: **no known vulnerabilities** (run 2026-10-26) | ✔ |
| Infrastructure | DB on an `internal` network with no published port; containers non-root, read-only FS, `cap_drop: ALL`; backups `chmod 600`, optional GPG | *compose validated, image not built here* |

## Not covered / residual risks (be explicit)

* No external DAST tool (ZAP/Burp) was available; dynamic testing was hand-written probes and Django test-client tests.
* The Swagger UI page (`/api/docs/`, staff-only) was not exercised in a browser under the strict CSP; `/api/schema/` works.
* CSP keeps `style-src 'unsafe-inline'` (inline `style=` attributes); scripts are strict.
* API tokens (`rest_framework.authtoken`) do not expire; revoke by deleting the token. Throttle: 600/min per user.
* Lock-out is per username+IP; behind a proxy set `FORWARDED_ALLOW_IPS`/trust only your proxy so the real client IP is seen.
* Single business unit: any permitted cashier can open any invoice (no per-branch scoping exists in the data model).
* Docker image build, Trivy-style image scan and TLS termination were not run (no Docker daemon in the build sandbox).
* Backups are encrypted only if you set `BACKUP_GPG_RECIPIENT`/`BACKUP_PASSPHRASE_FILE`; key management is yours.
