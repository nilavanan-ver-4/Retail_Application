#!/usr/bin/env bash
# Back up the PostgreSQL database and uploaded media, with a verification manifest.
#
#   DATABASE_URL           required  (postgres://user:pass@host:5432/dbname)
#   BACKUP_DIR             default ./backups
#   MEDIA_ROOT             default ./media
#   RETENTION_DAYS         default 14 (older backup sets are deleted)
#   BACKUP_GPG_RECIPIENT   optional: encrypt with this public key  (or)
#   BACKUP_PASSPHRASE_FILE optional: symmetric AES-256 with the passphrase in that file
#
# A successful exit means files were written. It does NOT mean the backup is good:
# run scripts/verify_restore.sh on it (cron/CI does this automatically).
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL must be set}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
MEDIA_ROOT="${MEDIA_ROOT:-./media}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
PYTHON="${PYTHON:-python}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
set_dir="${BACKUP_DIR:?}/${stamp:?}"
umask 077
mkdir -p "${set_dir:?}"

echo "[backup] dumping database"
pg_dump --format=custom --no-owner --no-privileges --file="${set_dir:?}/db.dump" "${DATABASE_URL}"

echo "[backup] archiving media"
if [ -d "${MEDIA_ROOT}" ]; then
  tar -C "$(dirname "${MEDIA_ROOT}")" -czf "${set_dir:?}/media.tar.gz" "$(basename "${MEDIA_ROOT}")"
else
  tar -czf "${set_dir:?}/media.tar.gz" --files-from /dev/null
fi

echo "[backup] recording verification manifest"
DATABASE_URL="${DATABASE_URL}" MEDIA_ROOT="${MEDIA_ROOT}" "${PYTHON}" manage.py data_snapshot 2>/dev/null \
  | sed -n '/^{/,$p' > "${set_dir:?}/manifest.json"

if [ -n "${BACKUP_GPG_RECIPIENT:-}" ]; then
  for f in db.dump media.tar.gz; do
    gpg --batch --yes --trust-model always --encrypt --recipient "${BACKUP_GPG_RECIPIENT}" \
        --output "${set_dir:?}/${f:?}.gpg" "${set_dir:?}/${f:?}"
    rm -f -- "${set_dir:?}/${f:?}"
  done
elif [ -n "${BACKUP_PASSPHRASE_FILE:-}" ]; then
  for f in db.dump media.tar.gz; do
    gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
        --pinentry-mode loopback --output "${set_dir:?}/${f:?}.gpg" "${set_dir:?}/${f:?}"
    rm -f -- "${set_dir:?}/${f:?}"
  done
fi

( cd "${set_dir:?}" && sha256sum ./* > SHA256SUMS )
chmod -R go-rwx "${set_dir:?}"

echo "[backup] pruning sets older than ${RETENTION_DAYS} days"
find "${BACKUP_DIR:?}" -mindepth 1 -maxdepth 1 -type d -mtime "+${RETENTION_DAYS}" -print0 \
  | xargs -0 -r rm -r --

echo "[backup] done: ${set_dir}"
echo "${set_dir}"
