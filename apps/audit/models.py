from django.conf import settings
from django.db import models


class AuditLogQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Audit log entries are immutable.")

    def delete(self):
        raise PermissionError("Audit log entries cannot be deleted.")


class AuditLog(models.Model):
    """Append-only record of sensitive actions (prices, stock, cancellations,
    payments, roles, logins). Never stores passwords or card data."""

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                             on_delete=models.SET_NULL, related_name="+")
    username = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    summary = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        db_table = "audit_log"
        ordering = ["-timestamp", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("Audit log entries are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit log entries cannot be deleted.")

    def __str__(self):
        return f"{self.timestamp:%Y-%m-%d %H:%M} {self.username} {self.action}"
