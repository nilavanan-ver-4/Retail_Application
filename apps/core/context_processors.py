from django.conf import settings


def business(request):
    from apps.products.models import TaxConfiguration

    tax = TaxConfiguration.current()
    return {"BUSINESS": {**settings.BUSINESS, "TAX_LABEL": tax.label, "TAX_ID": tax.registration_number}}
