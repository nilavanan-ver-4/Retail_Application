import json
import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from django.conf import settings

ROOT = Path(__file__).resolve().parents[2]


def _admin_conn(dbname="postgres"):
    d = settings.DATABASES["default"]
    return psycopg.connect(
        dbname=dbname,
        user=d["USER"],
        password=d["PASSWORD"],
        host=d["HOST"] or "localhost",
        port=d["PORT"] or 5432,
        autocommit=True,
    )


@pytest.fixture(scope="session")
def legacy_run(tmp_path_factory):
    """Build a fresh legacy-schema database from legacy_flask/DB/*.sql and drive the ORIGINAL
    Flask code through the recorded scenario. Returns (db name, observed JSON)."""
    pytest.importorskip("flask")
    pytest.importorskip("psycopg2")
    pytest.importorskip("pandas")
    name = f"legacy_char_{os.getpid()}"
    d = settings.DATABASES["default"]
    with _admin_conn() as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.execute(f'CREATE DATABASE "{name}"')
    with _admin_conn(name) as c:
        for f in ("table.sql", "trg.sql"):
            c.execute((ROOT / "legacy_flask" / "DB" / f).read_text())
    out = tmp_path_factory.mktemp("legacy") / "observed.json"
    env = {
        **os.environ,
        "LEGACY_PG_DB": name,
        "LEGACY_PG_USER": d["USER"],
        "LEGACY_PG_PASSWORD": d["PASSWORD"],
        "LEGACY_PG_HOST": d["HOST"] or "localhost",
        "LEGACY_PG_PORT": str(d["PORT"] or 5432),
    }
    res = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tests/characterization/legacy_char.py"),
            str(ROOT / "legacy_flask"),
            str(out),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=ROOT / "legacy_flask",
    )
    assert out.exists(), res.stdout[-2000:] + res.stderr[-2000:]
    yield name, json.loads(out.read_text())
    with _admin_conn() as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def legacy_cursor(legacy_run):
    """Read-only cursor on the legacy DB (same ``default_transaction_read_only`` guard the importer's
    ``legacy`` alias is configured with)."""
    name, _ = legacy_run
    d = settings.DATABASES["default"]
    conn = psycopg.connect(
        dbname=name,
        user=d["USER"],
        password=d["PASSWORD"],
        host=d["HOST"] or "localhost",
        port=d["PORT"] or 5432,
        options="-c default_transaction_read_only=on",
    )
    try:
        yield conn.cursor()
    finally:
        conn.close()
