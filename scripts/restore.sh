#!/usr/bin/env bash
# Restore a backup set into an EXISTING, EMPTY database (disaster recovery). Refuses to overwrite data.
#   usage: DATABASE_URL=... scripts/restore.sh <backup set dir> [media target dir]
set -euo pipefail
set_dir="${1:?usage: restore.sh <backup set dir> [media dir]}"
: "${DATABASE_URL:?DATABASE_URL must be set}"
media_target="${2:-${MEDIA_ROOT:-./media}}"
count="$(psql "${DATABASE_URL}" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")"
if [ "${count}" != "0" ]; then
  echo "Refusing to restore: the target database already has ${count} tables. Restore into an empty database." >&2
  exit 2
fi
( cd "${set_dir}" && sha256sum --check --quiet SHA256SUMS )
pg_restore --no-owner --no-privileges --exit-on-error --dbname="${DATABASE_URL}" "${set_dir}/db.dump"
mkdir -p "${media_target}"
tar -xzf "${set_dir}/media.tar.gz" -C "$(dirname "${media_target}")"
echo "Restored. Now run:  python manage.py data_snapshot   and compare with ${set_dir}/manifest.json"
