from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import TemplateView

from apps.core.mixins import LoginRequiredMixin


def health(request):
    """Liveness + DB readiness probe (no authentication, no sensitive data)."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:  # noqa: BLE001
        return JsonResponse({"status": "database unavailable"}, status=503)


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    return render(request, "errors/500.html", status=500)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard.html"
