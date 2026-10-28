#!/usr/bin/env bash
# Restore a backup set into a THROWAWAY database and prove it is usable:
#   * checksums match            * pg_restore succeeds
#   * counts and money totals equal the manifest recorded at backup time
#   * integrity checks pass (stock ledger, orphans, invoice equation, media references)
# Exits non-zero on any difference. Never touches the source database.
#
#   usage: scripts/verify_restore.sh <backup set dir>
#   ADMIN_DATABASE_URL  connection (with CREATEDB) used to create/drop the scratch database;
#                       default: DATABASE_URL pointed at the 'postgres' database
set -euo pipefail
set_dir="${1:?usage: verify_restore.sh <backup set dir>}"
: "${DATABASE_URL:?DATABASE_URL must be set (used to locate the server and credentials)}"
PYTHON="${PYTHON:-python}"
scratch="retail_verify_$(date +%s)_$$"
tmp="$(mktemp -d)"

with_db() {  # with_db <url> <dbname>  ->  url pointing at that database
  "${PYTHON}" -c 'import sys, urllib.parse as u; p = u.urlparse(sys.argv[1]); print(u.urlunparse(p._replace(path="/" + sys.argv[2])))' "$1" "$2"
}
admin_url="${ADMIN_DATABASE_URL:-$(with_db "${DATABASE_URL}" postgres)}"
scratch_url="$(with_db "${admin_url}" "${scratch}")"

cleanup() {
  psql "${admin_url}" -qc "DROP DATABASE IF EXISTS \"${scratch}\"" >/dev/null 2>&1 || true
  if [ -n "${tmp:-}" ] && [ -d "${tmp}" ]; then rm -r -- "${tmp:?}"; fi
}
trap cleanup EXIT

echo "[verify] checking checksums"
( cd "${set_dir}" && sha256sum --check --quiet SHA256SUMS )

dump="${set_dir}/db.dump"; media="${set_dir}/media.tar.gz"
if [ -f "${dump}.gpg" ]; then
  : "${BACKUP_PASSPHRASE_FILE:?encrypted backup: set BACKUP_PASSPHRASE_FILE}"
  gpg --batch --yes --passphrase-file "${BACKUP_PASSPHRASE_FILE}" --pinentry-mode loopback -o "${tmp}/db.dump" -d "${dump}.gpg"
  gpg --batch --yes --passphrase-file "${BACKUP_PASSPHRASE_FILE}" --pinentry-mode loopback -o "${tmp}/media.tar.gz" -d "${media}.gpg"
  dump="${tmp}/db.dump"; media="${tmp}/media.tar.gz"
fi

echo "[verify] restoring into scratch database ${scratch}"
psql "${admin_url}" -qc "CREATE DATABASE \"${scratch}\""
pg_restore --no-owner --no-privileges --exit-on-error --dbname="${scratch_url}" "${dump}"
mkdir -p "${tmp}/media_extract"
tar -xzf "${media}" -C "${tmp}/media_extract"
restored_media="${tmp}/media_extract/$(ls "${tmp}/media_extract" | head -n1)"

echo "[verify] comparing restored data with the manifest"
DATABASE_URL="${scratch_url}" MEDIA_ROOT="${restored_media}" "${PYTHON}" manage.py data_snapshot 2>/dev/null \
  | sed -n '/^{/,$p' > "${tmp}/restored.json"
"${PYTHON}" - "${set_dir}/manifest.json" "${tmp}/restored.json" <<'PY'
import json
import sys

want, got = (json.load(open(p)) for p in sys.argv[1:3])
problems = []
for section in ("counts", "totals"):
    for k, v in want[section].items():
        if got[section].get(k) != v:
            problems.append(f"{section}.{k}: backup manifest {v} != restored {got[section].get(k)}")
for k, v in got["checks"].items():
    if v not in (0, []):
        problems.append(f"integrity check failed: {k} = {v}")
if problems:
    print("RESTORE VERIFICATION FAILED:\n  " + "\n  ".join(problems))
    sys.exit(1)
print("RESTORE VERIFIED:", json.dumps(got["counts"]))
PY
