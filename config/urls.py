from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.static import serve

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("products/", include("apps.products.urls")),
    path("tax/", include("apps.products.tax_urls")),
    path("inventory/", include("apps.inventory.urls")),
    path("customers/", include("apps.customers.urls")),
    path("suppliers/", include("apps.suppliers.urls")),
    path("purchasing/", include("apps.purchasing.urls")),
    path("sales/", include("apps.sales.urls")),
    path("payments/", include("apps.payments.urls")),
    path("returns/", include("apps.returns.urls")),
    path("reports/", include("apps.reports.urls")),
    path("audit/", include("apps.audit.urls")),
    path("api/", include("apps.api.urls")),
    path("admin/", admin.site.urls),
]

handler403 = "apps.core.views.permission_denied"
handler404 = "apps.core.views.not_found"
handler500 = "apps.core.views.server_error"

if settings.DEBUG:
    # In production media is served by the reverse proxy (see docker/nginx.conf).
    urlpatterns += [path("media/<path:path>", serve, {"document_root": settings.MEDIA_ROOT})]
