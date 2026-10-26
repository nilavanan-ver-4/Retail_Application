from django.urls import path

from . import views

app_name = "inventory"
urlpatterns = [
    path("", views.StockLevelsView.as_view(), name="stock"),
    path("movements/", views.MovementListView.as_view(), name="movements"),
    path("adjust/", views.AdjustStockView.as_view(), name="adjust"),
    path("stocktakes/", views.StockTakeListView.as_view(), name="stocktakes"),
    path("stocktakes/<int:pk>/", views.StockTakeView.as_view(), name="stocktake"),
    path("reconcile/", views.ReconcileView.as_view(), name="reconcile"),
]
