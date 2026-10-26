from django.db import models


class ReportAccess(models.Model):
    """Holds report permissions only (no table is created)."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("view_sales_reports", "Can view sales reports"),
            ("view_inventory_reports", "Can view inventory & purchasing reports"),
            ("view_financial_reports", "Can view payments, receivables and profit reports"),
            ("view_audit_reports", "Can view the transaction audit report"),
        ]
