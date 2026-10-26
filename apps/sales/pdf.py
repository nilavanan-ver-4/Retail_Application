import base64
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string


def safe_fetcher():
    """Only inline ``data:`` URIs may be loaded while rendering a PDF; http(s):// and file://
    are refused (SSRF / local-file-read hardening - documents embed their own assets)."""
    from weasyprint.urls import URLFetcher
    return URLFetcher(allowed_protocols={"data"})


def logo_data_uri():
    path = Path(settings.BASE_DIR) / "static" / "img" / "logo.jpg"
    if not path.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode()


def render_pdf(template, context) -> bytes:
    from weasyprint import HTML

    css = (Path(settings.BASE_DIR) / "static" / "css" / "invoice.css").read_text(encoding="utf-8")
    html = render_to_string(template, {**context, "logo_uri": logo_data_uri(), "for_pdf": True,
                                       "invoice_css": css})
    return HTML(string=html, url_fetcher=safe_fetcher()).write_pdf()
