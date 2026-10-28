# Operations guide

All commands run from the repository root with the virtualenv active and environment variables set
(`.env` or exported). Never commit `.env`.

## 1. First install (production, Docker Compose)

```bash
cp .env.example .env     # then edit: SECRET_KEY, POSTGRES_PASSWORD, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, EMAIL_*, COMPANY_*
docker compose build
docker compose up -d db
docker compose run --rm migrate                     # migrate + setup_roles
docker compose run --rm web env BOOTSTRAP_ADMIN_USERNAME=admin BOOTSTRAP_ADMIN_PASSWORD='…' python manage.py bootstrap_admin
docker compose up -d
curl -fsS http://localhost/healthz/                 # {"status": "ok"}
```
Terminate TLS in front of Nginx (or add a `listen 443 ssl` block) and set `SECURE_SSL_REDIRECT=true`.
The PostgreSQL container publishes no port; open one only for a deliberate, firewalled admin need.

Without Docker: `pip install -r requirements/base.txt`, `python manage.py migrate && collectstatic`, run
`gunicorn config.wsgi -c gunicorn.conf.py` behind Nginx; serve `MEDIA_ROOT` from Nginx (see `docker/nginx.conf`).

## 2. Database migrations

* Create: `python manage.py makemigrations` (CI fails if models and migrations differ).
* Apply: back up first (§3) → `python manage.py migrate`. Migrations here are additive; to undo one:
  `python manage.py migrate <app> <previous>` (test on staging first).
* Legacy data: see [MIGRATION.md](MIGRATION.md).

## 3. Backup, restore, verification

```bash
export DATABASE_URL=… MEDIA_ROOT=… BACKUP_DIR=/var/backups/retail RETENTION_DAYS=14
# optional encryption: BACKUP_GPG_RECIPIENT=<key id>   or   BACKUP_PASSPHRASE_FILE=/root/.backup-pass (chmod 600)
scripts/backup.sh                      # db.dump + media.tar.gz + manifest.json + SHA256SUMS (mode 700/600)
scripts/verify_restore.sh <set dir>    # restores to a scratch DB, compares with the manifest, drops it
DATABASE_URL=<EMPTY target> scripts/restore.sh <set dir> [media dir]   # disaster recovery; refuses non-empty DBs
```
**A backup is only "good" when `verify_restore.sh` prints `RESTORE VERIFIED`.** It checks checksums, a real
`pg_restore`, record counts, money totals (invoices, payments, stock), the stock-ledger reconciliation, orphan
rows, the invoice equation, and that every product image referenced in the DB exists in the restored media.
Demonstrated in this build: good backup ✔ verified; corrupted dump ✘ detected (checksum); tampered manifest ✘
detected (count mismatch); restore into non-empty DB refused; DR restore equals manifest.

Schedule (cron, daily 02:15) and alert on non-zero exit:
`15 2 * * * cd /srv/retail && scripts/backup.sh && scripts/verify_restore.sh "$(ls -dt $BACKUP_DIR/*/ | head -1)"`.
Copy sets off-host (object storage with versioning). Test a full restore to another machine at least quarterly.

**Recovery recipes**
* *Accidental data change*: find the action in the Audit log; correct via the UI (reversal/adjustment/cancel
  are all audited). For bulk damage, restore the latest verified backup into a scratch DB and copy the rows back.
* *Failed migration*: `docker compose stop web`, restore the pre-migration backup into an empty DB, point `DATABASE_URL` at it, redeploy the previous image.
* *Bad deploy*: redeploy the previous image tag (§4); data schema is backward compatible within a release unless the release notes say otherwise.

## 4. Deploy / roll back

1. CI green on the commit (lint, bandit, pip-audit, tests, `check --deploy`, migrations check, backup round-trip, E2E, image build). The `Deploy` workflow refuses to publish otherwise.
2. `scripts/backup.sh` (+ verify).
3. `docker compose pull && docker compose run --rm migrate && docker compose up -d`
4. Health: `curl -fsS https://<host>/healthz/`; smoke: log in, open Invoices, create & cancel a test sale.
5. Roll back: `docker compose up -d` with the previous image tag; restore the backup only if a migration must be undone.

Keep the Flask rollback (`legacy_flask/`) and the pre-cut-over legacy dump for at least 30 days.

## 5. Users and roles
`manage.py setup_roles` (re)creates the six roles and runs automatically after each `migrate`.
Manage users at *Administration → Users*. Deactivate leavers — don't delete (audit history references them).

## 6. Monitoring and logs
* `/healthz/` (DB-checked) for load balancer / compose health. Logs are single-line `key=value` on stdout; request bodies are never logged.
* Watch: HTTP 429 spikes (lock-outs), `auth.login_failed` in the audit log, the Reconciliation report (should be empty), Gunicorn restarts, disk for `MEDIA_ROOT`/backups.

## 7. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ImproperlyConfigured: SECRET_KEY must be set` | Set `SECRET_KEY` (no fallback outside `DEBUG`) |
| `DisallowedHost` / 400 | Add the host to `ALLOWED_HOSTS` |
| 403 CSRF on login behind HTTPS proxy | Set `CSRF_TRUSTED_ORIGINS=https://your.host`, `TRUST_PROXY_SSL_HEADER=true` |
| Redirect loop | `SECURE_SSL_REDIRECT=true` without `TRUST_PROXY_SSL_HEADER=true` behind a TLS-terminating proxy |
| PDF fails with `cannot load library 'libpango…'` | `apt install libpango-1.0-0 libpangoft2-1.0-0` (included in the image) |
| HTTP 429 on login | Lock-out (5 failures); wait 15 min or `python manage.py axes_reset_username <user>` |
| `DatabaseError: new row violates check constraint "product_stock_non_negative"` | An operation tried to oversell/negative stock — correct data via an adjustment, don't disable the constraint |
| `import_legacy`: "refuses to merge into a non-empty target" | Use an empty DB (restore a clean one); by design |
| `import_legacy` aborts with quarantined rows | Read the report; fix at source or re-run with `--allow-quarantine` |
| Stock "doesn't add up" | *Inventory → Reconcile* compares balances with the movement ledger |
| Static files 404 in production | `python manage.py collectstatic --noinput` (done in the image) |
| Playwright cannot find browser | `playwright install chromium` or set `PLAYWRIGHT_CHROMIUM=/path/to/chrome` |
