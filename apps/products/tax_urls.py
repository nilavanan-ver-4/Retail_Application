from django.urls import path

from .tax_views import TaxSummaryView, TaxView

app_name = "taxes"
urlpatterns = [
    path("", TaxView.as_view(), name="index"),
    path("summary/", TaxSummaryView.as_view(), name="summary"),
]
