"""Centralised exception types and the REST exception handler."""

import logging

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger("retail")


class BusinessRuleError(Exception):
    """A user-correctable violation of a business rule (stock, payment, ...).

    Services raise this; views show the message, the API returns HTTP 400.
    """


class InsufficientStock(BusinessRuleError):
    def __init__(self, product, available, requested):
        self.product, self.available, self.requested = product, available, requested
        super().__init__(
            f"Not enough stock for {product.name} (requested {requested}, available {available})")


def api_exception_handler(exc, context):
    if isinstance(exc, BusinessRuleError):
        return Response({"detail": str(exc)}, status=400)
    if isinstance(exc, DjangoValidationError):
        return Response({"detail": exc.messages}, status=400)
    return exception_handler(exc, context)
