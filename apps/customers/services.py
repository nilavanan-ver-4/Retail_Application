from django.db import transaction
from django.db.models import Case, DecimalField, F, Sum, When
from django.utils import timezone

from apps.core.money import ZERO, q2

from .models import Customer, LedgerEntry


def ledger_balance(customer_id):
    row = LedgerEntry.objects.filter(customer_id=customer_id).aggregate(
        bal=Sum(Case(When(entry_type="Debit", then=F("amount")),
                     When(entry_type="Credit", then=-F("amount")),
                     output_field=DecimalField(max_digits=14, decimal_places=2))))
    return row["bal"] or ZERO


@transaction.atomic
def post_ledger_entry(customer_id, entry_type, amount, *, description="", user=None, invoice=None,
                      source=LedgerEntry.Source.SYSTEM, when=None):
    """Append a statement line. The customer row lock serialises concurrent
    postings so the running balance is always consistent."""
    Customer.objects.select_for_update().get(pk=customer_id)
    amount = q2(amount)
    balance = ledger_balance(customer_id) + (amount if entry_type == "Debit" else -amount)
    return LedgerEntry.objects.create(
        customer_id=customer_id, entry_date=when or timezone.now(), entry_type=entry_type,
        amount=amount, balance=balance, description=description, invoice=invoice,
        source=source, created_by=user)


def outstanding_for(customer_id):
    """Authoritative receivable: unpaid portion of every non-cancelled invoice."""
    from apps.sales.models import Invoice

    qs = Invoice.objects.filter(customer_id=customer_id).exclude(status=Invoice.Status.CANCELLED)
    agg = qs.aggregate(t=Sum("total"), r=Sum("returned_total"), p=Sum("total_paid"))
    return (agg["t"] or ZERO) - (agg["r"] or ZERO) - (agg["p"] or ZERO)
