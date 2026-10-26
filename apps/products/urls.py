from django.urls import path

from . import views

app_name = "products"
urlpatterns = [
    path("", views.ProductListView.as_view(), name="list"),
    path("new/", views.ProductCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ProductDetailView.as_view(), name="detail"),
    path("<int:pk>/edit/", views.ProductUpdateView.as_view(), name="update"),
    path("<int:pk>/archive/", views.ProductArchiveView.as_view(), name="archive"),
    path("<int:pk>/restore/", views.ProductArchiveView.as_view(restore=True), name="restore"),
    path("bulk/", views.ProductBulkView.as_view(), name="bulk"),
    path("export/", views.ProductExportView.as_view(), name="export"),
    path("import/", views.ProductImportUploadView.as_view(), name="import"),
    path("import/<int:pk>/", views.ProductImportPreviewView.as_view(), name="import_preview"),
    path("search/", views.ProductSearchView.as_view(), name="search"),
    path("categories/", views.CategoryListView.as_view(), name="categories"),
]
