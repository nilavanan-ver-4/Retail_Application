import django_filters as df
from django import forms

from apps.core.mixins import FilteredListView

from .models import AuditLog


class AuditFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Search")
    action = df.CharFilter(lookup_expr="icontains")
    date_from = df.DateFilter(
        field_name="timestamp", lookup_expr="date__gte", widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = df.DateFilter(
        field_name="timestamp", lookup_expr="date__lte", widget=forms.DateInput(attrs={"type": "date"})
    )

    class Meta:
        model = AuditLog
        fields = ["q", "action", "date_from", "date_to"]

    def search(self, qs, name, value):
        from django.db.models import Q

        return qs.filter(
            Q(username__icontains=value) | Q(summary__icontains=value) | Q(target_id__icontains=value)
        )


class AuditLogListView(FilteredListView):
    permission_required = "audit.view_auditlog"
    model = AuditLog
    filterset_class = AuditFilter
    template_name = "audit/list.html"
    title = "Audit log"
    paginate_by = 50

    def get_queryset(self):
        return super().get_queryset().select_related("user")
