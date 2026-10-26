from django.urls import path

from . import views

app_name = "suppliers"
urlpatterns = [
    path("", views.SupplierListView.as_view(), name="list"),
    path("new/", views.SupplierCreateView.as_view(), name="create"),
    path("<int:pk>/", views.SupplierDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.SupplierUpdateView.as_view(), name="update"),
    path("import/", views.SupplierImportUploadView.as_view(), name="import"),
    path("import/<int:pk>/", views.SupplierImportPreviewView.as_view(), name="import_preview"),
]
