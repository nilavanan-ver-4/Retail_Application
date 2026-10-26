"""Spreadsheet import engine: parse -> validate -> preview -> apply.

Policy (documented in docs/OPERATIONS.md): an import is **never applied
silently**. The preview lists every row with its action or errors. The user
must confirm; invalid rows are only skipped when they tick "skip invalid rows",
otherwise any error blocks the whole import. Applying runs in one transaction,
so a database failure rolls back the entire batch.
"""

import csv
import io
import re
from datetime import date, datetime

from django.conf import settings
from django.core.exceptions import ValidationError

MAX_ROWS = 5000
ALLOWED_EXTENSIONS = (".csv", ".xlsx")


def normalise_header(h):
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def read_table(uploaded):
    """Return (headers, rows) from an uploaded CSV/XLSX. Raises ValidationError."""
    name = (uploaded.name or "").lower()
    if not name.endswith(ALLOWED_EXTENSIONS):
        raise ValidationError("Only .csv and .xlsx files are accepted.")
    if uploaded.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        raise ValidationError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
    data = uploaded.read()
    try:
        if name.endswith(".csv"):
            text = data.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text))
            table = list(reader)
        else:
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            table = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
            wb.close()
    except UnicodeDecodeError as exc:
        raise ValidationError("CSV must be UTF-8 encoded.") from exc
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt/hostile workbook
        raise ValidationError("The file could not be read as a valid spreadsheet.") from exc
    table = [r for r in table if any(str(c).strip() for c in r)]
    if not table:
        raise ValidationError("The file is empty.")
    if len(table) - 1 > MAX_ROWS:
        raise ValidationError(f"Too many rows (maximum {MAX_ROWS}).")
    headers = [normalise_header(h) for h in table[0]]
    return headers, [[_cell(c) for c in r] for r in table[1:]]


class BaseImporter:
    kind = ""
    required = ()  # canonical column names that must be present
    aliases = {}  # legacy/alternate header -> canonical header
    modes = (("create", "Create new records only (duplicates are errors)"),)

    def canonical_headers(self, headers):
        return [self.aliases.get(h, h) for h in headers]

    def parse(self, uploaded, mode="create"):
        headers, rows = read_table(uploaded)
        headers = self.canonical_headers(headers)
        missing = [c for c in self.required if c not in headers]
        if missing:
            raise ValidationError(f"Missing required column(s): {', '.join(missing)}.")
        ctx = self.build_context(mode)
        seen = {}
        out = []
        for i, raw in enumerate(rows, start=2):
            row = dict(zip(headers, raw + [""] * (len(headers) - len(raw)), strict=False))
            data, errors, action = self.validate_row(row, mode, ctx)
            key = self.identity(data) if data else None
            if key is not None:
                if key in seen:
                    errors.append(f"Duplicate of row {seen[key]} in this file.")
                else:
                    seen[key] = i
            out.append(
                {"line": i, "data": data or {}, "action": action if not errors else "error", "errors": errors}
            )
        return out

    # hooks ------------------------------------------------------------------
    def build_context(self, mode):
        return {}

    def validate_row(self, row, mode, ctx):  # -> (clean_dict, [errors], action)
        raise NotImplementedError

    def identity(self, data):
        return None

    def apply(self, rows, user):  # rows: list of {"data","action"}
        raise NotImplementedError


def summarise(rows):
    s = {"total": len(rows), "errors": sum(1 for r in rows if r["errors"])}
    for r in rows:
        if not r["errors"]:
            s[r["action"]] = s.get(r["action"], 0) + 1
    return s
