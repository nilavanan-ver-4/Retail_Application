from decimal import Decimal

import pytest
from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.accounts.roles import ADMINISTRATOR, CASHIER, INVENTORY, MANAGER, PURCHASING, REPORTING, sync_roles
from apps.customers.models import Customer
from apps.inventory.models import StockMovement
from apps.inventory.services import apply_movement
from apps.products.models import Category, Product
from apps.suppliers.models import Supplier


@pytest.fixture(autouse=True)
def _fast_hashing(settings):
    # production uses PBKDF2; tests use a fast hasher purely for speed
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _roles(db):
    sync_roles()


def make_user(username, role=None, **kw):
    user = User.objects.create_user(username=username, password="S3cure-test-pw!", **kw)
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user


@pytest.fixture
def admin_user(db):
    return make_user("admin1", ADMINISTRATOR)


@pytest.fixture
def manager(db):
    return make_user("manager1", MANAGER)


@pytest.fixture
def cashier(db):
    return make_user("cashier1", CASHIER)


@pytest.fixture
def inventory_staff(db):
    return make_user("inv1", INVENTORY)


@pytest.fixture
def purchaser(db):
    return make_user("buyer1", PURCHASING)


@pytest.fixture
def reporter(db):
    return make_user("report1", REPORTING)


@pytest.fixture
def client_as(client):
    def _login(user):
        client.force_login(user)
        return client

    return _login


@pytest.fixture
def supplier(db):
    return Supplier.objects.create(
        name="ABC", company="ABC Pvt", mobile="9876543210", email="a@x.com", terms="Net 30"
    )


@pytest.fixture
def category(db):
    return Category.objects.create(name="Fruits")


@pytest.fixture
def make_product(db, category, supplier):
    counter = {"n": 0}

    def _make(name="Apple", price="70.00", cost="50.00", stock=100, tax="0.00", sku=None, reorder=20, **kw):
        counter["n"] += 1
        p = Product.objects.create(
            sku=sku or f"SKU{counter['n']:04d}",
            name=name,
            category=category,
            supplier=supplier,
            unit="kg",
            cost_price=Decimal(cost),
            selling_price=Decimal(price),
            tax_rate=Decimal(tax),
            reorder_level=reorder,
            **kw,
        )
        if stock:
            apply_movement(p.pk, StockMovement.Type.OPENING, stock)
            p.refresh_from_db()
        return p

    return _make


@pytest.fixture
def product(make_product):
    return make_product()


@pytest.fixture
def customer(db):
    return Customer.objects.create(name="Cust A", mobile="0123456789", billing_address="KL")
