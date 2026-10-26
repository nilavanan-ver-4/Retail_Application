from django.urls import path

from . import views

app_name = "customers"
urlpatterns = [
    path("", views.CustomerListView.as_view(), name="list"),
    path("new/", views.CustomerCreateView.as_view(), name="create"),
    path("<int:pk>/", views.CustomerDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="update"),
    path("export/", views.CustomerExportView.as_view(), name="export"),
    path("import/", views.CustomerImportUploadView.as_view(), name="import"),
    path("import/<int:pk>/", views.CustomerImportPreviewView.as_view(), name="import_preview"),
    path("ledger/", views.LedgerListView.as_view(), name="ledger"),
    path("ledger/new/", views.LedgerAddView.as_view(), name="ledger_add"),
    path("lookup/", views.CustomerLookupView.as_view(), name="lookup"),
]
