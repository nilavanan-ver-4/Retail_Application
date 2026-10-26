"""``log()`` records an audit entry using the current request context."""

import contextvars
import json
from decimal import Decimal

_ctx = contextvars.ContextVar("audit_ctx", default=None)

SENSITIVE_KEYS = {"password", "password1", "password2", "token", "secret", "card_number", "cvv"}


def set_context(user, ip):
    return _ctx.set({"user": user, "ip": ip})


def reset_context(token):
    _ctx.reset(token)


def _clean(value):
    if isinstance(value, dict):
        return {k: ("***" if k.lower() in SENSITIVE_KEYS else _clean(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def log(action, target=None, summary="", user=None, **details):
    from .models import AuditLog

    ctx = _ctx.get() or {}
    actor = user or ctx.get("user")
    if actor is not None and not getattr(actor, "is_authenticated", False):
        actor = None
    return AuditLog.objects.create(
        user=actor,
        username=getattr(actor, "username", "") or details.pop("username", ""),
        action=action,
        target_type=target._meta.label_lower if target is not None else "",
        target_id=str(target.pk) if target is not None and target.pk is not None else "",
        summary=(summary or (str(target) if target is not None else ""))[:255],
        details=_clean(details),
        ip_address=ctx.get("ip"),
    )
