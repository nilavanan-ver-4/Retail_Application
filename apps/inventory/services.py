"""Stock operations. The ONLY code allowed to change ``Product.stock_on_hand``.

Concurrency model: each movement locks the product row with SELECT ... FOR
UPDATE inside a transaction, re-reads the balance, validates it, then writes
the movement and the new balance atomically. Two simultaneous sales of the last
unit therefore serialise: the second one sees 0 and is rejected, so overselling
is impossible. The CHECK constraint on ``products_product`` is the last line of
defence if application code is ever bypassed.
"""

from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from apps.core.exceptions import BusinessRuleError, InsufficientStock
from apps.products.models import Product

from .models import StockMovement, StockTake

OUTBOUND_TYPES = {StockMovement.Type.SALE, StockMovement.Type.DAMAGE, StockMovement.Type.RETURN_DAMAGED}


def apply_movement(
    product_id,
    movement_type,
    quantity,
    *,
    user=None,
    reference=None,
    note="",
    unit_cost=None,
    when=None,
    locked_product=None,
) -> StockMovement:
    """Post a signed stock movement. ``quantity`` > 0 adds stock, < 0 removes it."""
    if not quantity:
        raise BusinessRuleError("Quantity must not be zero.")
    with transaction.atomic():
        product = locked_product or Product.objects.select_for_update().get(pk=product_id)
        new_balance = product.stock_on_hand + quantity
        if new_balance < 0 and not product.allow_negative_stock:
            raise InsufficientStock(product, product.stock_on_hand, -quantity)
        product.stock_on_hand = new_balance
        product.save(update_fields=["stock_on_hand", "updated_at"])
        ref_type, ref_id = reference if reference else ("", "")
        return StockMovement.objects.create(
            product=product,
            movement_type=movement_type,
            quantity=quantity,
            balance_after=new_balance,
            unit_cost=unit_cost,
            reference_type=ref_type,
            reference_id=str(ref_id),
            note=note[:255],
            created_by=user,
            created_at=when or timezone.now(),
        )


def lock_products(product_ids):
    """Lock several products in a deterministic order (prevents deadlocks when
    two invoices contain the same products in different order)."""
    products = {
        p.pk: p for p in Product.objects.select_for_update().filter(pk__in=set(product_ids)).order_by("pk")
    }
    return products


def adjust_stock(product_id, delta, *, kind, user, note):
    """Manual adjustment/write-off with mandatory reason, audited."""
    from apps.audit.services import log

    if not note.strip():
        raise BusinessRuleError("A reason is required for stock adjustments.")
    with transaction.atomic():
        movement = apply_movement(product_id, kind, delta, user=user, note=note)
        log(
            "stock.adjusted",
            movement.product,
            quantity=delta,
            kind=kind,
            reason=note,
            balance_after=movement.balance_after,
        )
    return movement


@transaction.atomic
def start_stocktake(name, user, products=None):
    st = StockTake.objects.create(name=name, created_by=user)
    qs = products if products is not None else Product.objects.filter(is_active=True)
    st.lines.bulk_create(
        [st.lines.model(stocktake=st, product=p, system_quantity=p.stock_on_hand) for p in qs]
    )
    return st


@transaction.atomic
def apply_stocktake(stocktake_id, user):
    from apps.audit.services import log

    st = StockTake.objects.select_for_update().get(pk=stocktake_id)
    if st.status != StockTake.Status.DRAFT:
        raise BusinessRuleError("This stock-take was already applied.")
    posted = 0
    for line in st.lines.select_related("product").order_by("product_id"):
        if line.counted_quantity is None:
            continue
        product = Product.objects.select_for_update().get(pk=line.product_id)
        delta = line.counted_quantity - product.stock_on_hand  # vs LIVE stock, not the snapshot
        if delta:
            apply_movement(
                product.pk,
                StockMovement.Type.COUNT,
                delta,
                user=user,
                reference=("stocktake", st.pk),
                note=f"Stock-take: {st.name}",
                locked_product=product,
            )
            posted += 1
    st.status = StockTake.Status.APPLIED
    st.applied_at = timezone.now()
    st.save(update_fields=["status", "applied_at", "updated_at"])
    log("stock.stocktake_applied", st, summary=f"{st.name}: {posted} corrections")
    return posted


def reconcile():
    """Compare each product's running balance with its movement ledger.
    Returns a list of (product, ledger_sum, stock_on_hand) mismatches."""
    from django.db.models import Sum

    sums = dict(StockMovement.objects.values_list("product_id").annotate(s=Sum("quantity")))
    bad = []
    for p in Product.objects.all().only("id", "sku", "name", "stock_on_hand"):
        ledger = sums.get(p.pk, 0) or 0
        if ledger != p.stock_on_hand:
            bad.append((p, ledger, p.stock_on_hand))
    return bad


def fifo_ageing(stock, inbound, today=None, buckets=(30, 60, 90)):
    """Age the CURRENT stock using FIFO: the newest receipts are assumed to
    still be on the shelf. ``inbound`` is a list of (date, quantity) receipts
    (opening/purchase/returns). Returns {bucket_label: quantity}."""
    today = today or timezone.localdate()
    remaining = stock
    result = defaultdict(int)
    labels = (
        [f"0-{buckets[0]} days"]
        + [f"{a + 1}-{b} days" for a, b in zip(buckets, buckets[1:], strict=False)]
        + [f"{buckets[-1]}+ days"]
    )
    for when, qty in sorted(inbound, key=lambda x: x[0], reverse=True):
        if remaining <= 0:
            break
        take = min(qty, remaining)
        remaining -= take
        age = (today - when).days
        idx = next((i for i, b in enumerate(buckets) if age <= b), len(buckets))
        result[labels[idx]] += take
    if remaining > 0:  # stock with no recorded receipt is the oldest
        result[labels[-1]] += remaining
    return {label: result.get(label, 0) for label in labels}
