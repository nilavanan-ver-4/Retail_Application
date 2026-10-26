"""Role definitions. Roles are Django groups; each maps to explicit permissions.

All enforcement happens server-side in views / DRF permission classes via these
permissions - hiding a menu item is never the only protection.
"""

from django.contrib.auth.models import Group, Permission

ADMINISTRATOR = "Administrator"
MANAGER = "Manager"
CASHIER = "Cashier"
INVENTORY = "Inventory Staff"
PURCHASING = "Purchasing Staff"
REPORTING = "Reporting"


def _crud(app, model, actions="add change view"):
    return [f"{app}.{a}_{model}" for a in actions.split()]


_PRODUCT_READ = ["products.view_product", "products.view_category", "products.view_brand"]
_CUSTOMER_FULL = _crud("customers", "customer") + _crud("customers", "ledgerentry", "view")
_REPORT_ALL = [
    "reports.view_sales_reports", "reports.view_inventory_reports",
    "reports.view_financial_reports", "reports.view_audit_reports",
]

ROLE_PERMISSIONS = {
    CASHIER: [
        *_PRODUCT_READ,
        "customers.view_customer", "customers.add_customer", "customers.change_customer",
        "sales.view_invoice", "sales.add_invoice",
        "payments.view_payment", "payments.add_payment",
        "returns.view_salesreturn", "returns.add_salesreturn",
    ],
    INVENTORY: [
        *_crud("products", "product"), *_crud("products", "category"), *_crud("products", "brand"),
        "products.view_cost_price", "products.import_products", "products.export_products",
        "inventory.view_stockmovement", "inventory.adjust_stock",
        *_crud("inventory", "stocktake"),
        "reports.view_inventory_reports",
    ],
    PURCHASING: [
        *_PRODUCT_READ, "products.view_cost_price",
        *_crud("suppliers", "supplier"),
        *_crud("purchasing", "purchaseorder"), *_crud("purchasing", "goodsreceipt"),
        "purchasing.receive_goods",
        *_crud("purchasing", "supplierinvoice"), *_crud("purchasing", "supplierpayment"),
        "inventory.view_stockmovement",
        "reports.view_inventory_reports",
    ],
    REPORTING: [
        *_PRODUCT_READ,
        "customers.view_customer", "suppliers.view_supplier",
        "sales.view_invoice", "payments.view_payment",
        "purchasing.view_purchaseorder", "purchasing.view_supplierinvoice",
        "inventory.view_stockmovement",
        "returns.view_salesreturn",
        "reports.view_sales_reports", "reports.view_inventory_reports",
        "reports.view_financial_reports",
    ],
}
ROLE_PERMISSIONS[MANAGER] = sorted(set(
    ROLE_PERMISSIONS[CASHIER] + ROLE_PERMISSIONS[INVENTORY] + ROLE_PERMISSIONS[PURCHASING]
    + ROLE_PERMISSIONS[REPORTING] + _CUSTOMER_FULL + [
        "customers.add_ledgerentry", "customers.override_credit_limit",
        "sales.cancel_invoice", "sales.override_price",
        "payments.reverse_payment", "payments.view_refund",
        "returns.approve_return", "returns.change_salesreturn",
        "inventory.adjust_stock",
        "reports.view_audit_reports",
        "audit.view_auditlog",
    ]))
# Administrator = every business permission plus user management and audit.
ADMIN_EXTRA = [
    *_crud("accounts", "user", "add change view"),
    "auth.view_group", "audit.view_auditlog",
]


def _perm(label):
    app, codename = label.split(".", 1)
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


def sync_roles():
    """Create/refresh the role groups. Idempotent; safe to run on every migrate."""
    for role, labels in {**ROLE_PERMISSIONS,
                         ADMINISTRATOR: sorted(set(sum(ROLE_PERMISSIONS.values(), []) + ADMIN_EXTRA))
                         }.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = [p for p in (_perm(label) for label in labels) if p is not None]
        group.permissions.set(perms)
