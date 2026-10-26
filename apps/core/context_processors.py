from django.conf import settings


def business(request):
    return {"BUSINESS": settings.BUSINESS}
