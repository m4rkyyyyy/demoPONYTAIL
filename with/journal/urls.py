from django.urls import path

from . import views

urlpatterns = [
    path("", views.TradeListView.as_view(), name="trade_list"),
    path("new/", views.TradeCreateView.as_view(), name="trade_create"),
    path("export/", views.trade_export, name="trade_export"),
    path("import/", views.trade_import, name="trade_import"),
    path("<int:pk>/edit/", views.TradeUpdateView.as_view(), name="trade_edit"),
    path("<int:pk>/delete/", views.TradeDeleteView.as_view(), name="trade_delete"),
]