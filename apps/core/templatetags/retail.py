from decimal import Decimal

from django import template
from django.conf import settings
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def money(value, symbol=True):
    if value is None or value == "":
        return "-"
    try:
        text = f"{Decimal(value):,.2f}"
    except Exception:  # noqa: BLE001
        return value
    return f"{settings.BUSINESS['CURRENCY']} {text}" if symbol else text


@register.filter
def getattr_path(obj, path):
    """Resolve 'a.b.c' (calling callables) - used by the generic list template."""
    for part in str(path).split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
        if callable(obj):
            obj = obj()
    return obj


@register.filter
def get_item(d, key):
    return d.get(key) if hasattr(d, "get") else None


@register.simple_tag
def badge(text, kind="neutral"):
    return format_html('<span class="badge badge-{}">{}</span>', kind, text)


@register.filter
def status_kind(value):
    v = str(value).lower()
    if v in ("paid", "completed", "received", "confirmed", "approved", "applied", "active", "completed"):
        return "ok"
    if v in ("pending", "partially paid", "partial", "draft", "ordered", "unpaid"):
        return "warn"
    if v in ("cancelled", "rejected", "reversed", "returned", "inactive", "archived"):
        return "bad"
    return "neutral"


@register.simple_tag
def bar_chart(labels, values, title="", width=640, height=220):
    """Accessible server-rendered SVG bar chart (no JavaScript, CSP-safe)."""
    vals = [float(v or 0) for v in values]
    if not vals or max(vals) <= 0:
        return mark_safe('<p class="empty">No data for this period.</p>')
    pad_l, pad_b, pad_t = 48, 36, 12
    plot_w, plot_h = width - pad_l - 8, height - pad_b - pad_t
    top = max(vals)
    n = len(vals)
    bw = plot_w / n
    parts = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{title}">',
             f"<title>{title}</title>"]
    for frac in (0, 0.5, 1):
        y = pad_t + plot_h - plot_h * frac
        parts.append(f'<line class="grid" x1="{pad_l}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}"/>')
        parts.append(f'<text class="axis" x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">{top * frac:,.0f}</text>')
    for i, (lab, v) in enumerate(zip(labels, vals, strict=False)):
        h = plot_h * v / top
        x = pad_l + i * bw + bw * 0.15
        parts.append(f'<rect class="bar" x="{x:.1f}" y="{pad_t + plot_h - h:.1f}" width="{bw * 0.7:.1f}" '
                     f'height="{h:.1f}" rx="2"><title>{lab}: {v:,.2f}</title></rect>')
        if n <= 14 or i % max(n // 10, 1) == 0:
            parts.append(f'<text class="axis" x="{x + bw * 0.35:.1f}" y="{height - 14}" '
                         f'text-anchor="middle">{str(lab)[:10]}</text>')
    parts.append("</svg>")
    return mark_safe("".join(parts))


@register.simple_tag
def hbar_list(rows, label_key=0, value_key=1):
    """Horizontal bars as plain HTML/CSS-free markup using <progress>-like SVG."""
    rows = list(rows)
    if not rows:
        return mark_safe('<p class="empty">No data for this period.</p>')
    top = max(float(r[value_key] or 0) for r in rows) or 1
    out = ['<ul class="hbars">']
    for r in rows:
        pct = float(r[value_key] or 0) / top * 100
        out.append(format_html(
            '<li><span class="hb-label">{}</span><svg class="hb" viewBox="0 0 100 8" preserveAspectRatio="none" '
            'aria-hidden="true"><rect width="100" height="8" class="hb-bg"/><rect width="{}" height="8" '
            'class="hb-fg"/></svg><span class="hb-val">{}</span></li>',
            r[label_key], f"{pct:.1f}", f"{float(r[value_key] or 0):,.2f}"))
    out.append("</ul>")
    return mark_safe("".join(out))


@register.filter
def get_col_kind(columns, index):
    try:
        return columns[index][1]
    except (IndexError, TypeError):
        return "text"
