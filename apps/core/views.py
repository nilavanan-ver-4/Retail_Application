from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.generic import TemplateView

from django.contrib.auth.mixins import LoginRequiredMixin


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

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        from apps.reports.queries import dashboard_data
        ctx = super().get_context_data(**kwargs)
        u = self.request.user
        ctx["today"] = timezone.localdate()
        # real database figures; users who cannot see sales/stock get an empty welcome panel
        ctx["d"] = dashboard_data(ctx["today"]) if (u.has_perm("sales.view_invoice") or u.has_perm("products.view_product")) else None
        return ctx
