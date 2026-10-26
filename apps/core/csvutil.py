"""CSV export helpers."""

import csv

from django.http import HttpResponse

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def safe_cell(value):
    """Neutralise spreadsheet formula injection (CSV injection / OWASP).

    Numbers stay numbers; text that would be interpreted as a formula by Excel
    or LibreOffice is prefixed with an apostrophe.
    """
    if isinstance(value, str) and value.startswith(_DANGEROUS):
        return "'" + value
    return "" if value is None else value


def csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow([safe_cell(c) for c in row])
    return response
