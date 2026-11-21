from django.contrib import admin


class HistoricalDocumentAdmin(admin.ModelAdmin):
    """Inspect transaction records; mutations must use the audited service workflows."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
