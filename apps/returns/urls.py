from django.urls import path

from . import views

app_name = "returns"
urlpatterns = [
    path("", views.ReturnListView.as_view(), name="list"),
    path("new/", views.ReturnCreateView.as_view(), name="create"),
    path("<int:pk>/", views.ReturnDetailView.as_view(), name="detail"),
    path("<int:pk>/<str:decision>/", views.ReturnDecisionView.as_view(), name="decide"),
]
