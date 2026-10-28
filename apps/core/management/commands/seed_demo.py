"""Load SYNTHETIC demo/performance data. Never run against a real business database.

python manage.py seed_demo --password 'S0me-long-Demo-pw' --products 200 --customers 100 --invoices 300
"""

import random
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.roles import ADMINISTRATOR, CASHIER, INVENTORY, MANAGER, PURCHASING, REPORTING, sync_roles
from apps.core.exceptions import BusinessRuleError
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement
from apps.products.models import Category, Product
from apps.sales.services import create_invoice
from apps.suppliers.models import Supplier

CATEGORIES = ["Grocery", "Dairy", "Fruits", "Beverages", "Household", "Snacks", "Frozen", "Bakery"]


class Command(BaseCommand):
    help = "Create synthetic users, products, customers and invoices for demos and load tests."

    def add_arguments(self, p):
        p.add_argument(
            "--password", required=True, help="Password for every demo user (not stored anywhere else)."
        )
        p.add_argument("--products", type=int, default=100)
        p.add_argument("--customers", type=int, default=50)
        p.add_argument("--invoices", type=int, default=100)
        p.add_argument("--seed", type=int, default=42)

    @transaction.atomic
    def handle(self, *args, **o):
        if Product.objects.exists() and not o.get("force", False):
            raise CommandError(
                "Database already has products; refusing to add demo data on top of real data."
            )
        rnd = random.Random(o["seed"])
        sync_roles()
        for username, role in (
            ("demo_admin", ADMINISTRATOR),
            ("demo_manager", MANAGER),
            ("demo_cashier", CASHIER),
            ("demo_inventory", INVENTORY),
            ("demo_buyer", PURCHASING),
            ("demo_reports", REPORTING),
        ):
            u, _ = User.objects.get_or_create(
                username=username, defaults={"email": f"{username}@example.test"}
            )
            u.set_password(o["password"])
            u.is_staff = role == ADMINISTRATOR
            u.save()
            u.groups.set([Group.objects.get(name=role)])
        sup = [
            Supplier.objects.create(
                name=f"Supplier {i}",
                company=f"Supplier {i} Sdn Bhd",
                mobile=f"01200000{i:02d}",
                email=f"s{i}@example.test",
            )
            for i in range(1, 6)
        ]
        cats = [Category.objects.create(name=n) for n in CATEGORIES]
        manager = User.objects.get(username="demo_manager")
        products = []
        for i in range(1, o["products"] + 1):
            cost = Decimal(rnd.randint(100, 5000)) / 100
            p = Product.objects.create(
                sku=f"DEMO{i:05d}",
                name=f"Demo product {i:04d}",
                category=rnd.choice(cats),
                supplier=rnd.choice(sup),
                unit=rnd.choice(["pcs", "kg", "box"]),
                cost_price=cost,
                selling_price=(cost * Decimal("1.35")).quantize(Decimal("0.01")),
                reorder_level=rnd.randint(5, 30),
                tax_rate=rnd.choice([Decimal("0"), Decimal("6"), Decimal("10")]),
            )
            apply_movement(
                p.pk,
                StockMovement.Type.OPENING,
                rnd.randint(50, 2000),
                user=manager,
                note="demo opening stock",
            )
            products.append(p.pk)
        customers = [
            Customer.objects.create(name=f"Demo customer {i:04d}", mobile=f"0{1100000000 + i}").pk
            for i in range(1, o["customers"] + 1)
        ]
        made = 0
        today = timezone.localdate()
        for _ in range(o["invoices"]):
            lines = [
                {"product_id": pid, "quantity": rnd.randint(1, 5)}
                for pid in rnd.sample(products, rnd.randint(1, 5))
            ]
            try:
                inv, _ = create_invoice(
                    user=manager,
                    customer=Customer.objects.get(pk=rnd.choice(customers)),
                    lines=lines,
                    invoice_date=today - timedelta(days=rnd.randint(0, 60)),
                    discount_percent=Decimal(rnd.choice([0, 0, 0, 5, 10])),
                )
                if rnd.random() < 0.6:
                    from apps.payments.services import record_payment

                    record_payment(
                        inv.pk,
                        amount=inv.balance_due,
                        method=rnd.choice(["cash", "upi", "credit_card"]),
                        user=manager,
                    )
                made += 1
            except BusinessRuleError:
                continue
        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(products)} products, {len(customers)} customers, {made} invoices; demo users: demo_admin, "
                "demo_manager, demo_cashier, demo_inventory, demo_buyer, demo_reports"
            )
        )
