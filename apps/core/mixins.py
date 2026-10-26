"""Reusable view building blocks: permissions, filtered lists, CSV export."""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.views.generic import ListView


class AppPermissionMixin(LoginRequiredMixin, PermissionRequiredMixin):
    """Authenticated users lacking the permission get a 403, anonymous users
    are redirected to login. Enforcement is server-side on every request."""

    raise_exception = False

    def handle_no_permission(self):
        if self.request.user.is_authenticated:
            raise PermissionDenied
        return super().handle_no_permission()


class FilteredListView(AppPermissionMixin, ListView):
    """ListView with django-filter integration, search and pagination."""

    paginate_by = 25
    filterset_class = None
    template_name = "generic/list.html"
    title = ""
    columns = ()  # (header, attr-or-callable) for the generic table

    actions = ()  # (label, url_name, permission or None, css_kind)

    def get_columns(self):
        return [{"label": c[0], "attr": c[1], "kind": c[2] if len(c) > 2 else "text",
                 "link": i == 0 and (len(c) < 4 or c[3] != "nolink")}
                for i, c in enumerate(self.columns)]

    def get_actions(self):
        from django.urls import reverse
        out = []
        for a in self.actions:
            label, name, perm = a[0], a[1], a[2] if len(a) > 2 else None
            if perm is None or self.request.user.has_perm(perm):
                out.append({"label": label, "url": reverse(name), "kind": a[3] if len(a) > 3 else ""})
        return out

    def get_queryset(self):
        qs = super().get_queryset()
        if self.filterset_class:
            self.filterset = self.filterset_class(self.request.GET or None, queryset=qs,
                                                  request=self.request)
            qs = self.filterset.qs
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["filter"] = getattr(self, "filterset", None)
        ctx["title"] = self.title
        params = self.request.GET.copy()
        params.pop("page", None)
        ctx["querystring"] = params.urlencode()
        return ctx


def flash_business_error(request, exc):
    messages.error(request, str(exc))
