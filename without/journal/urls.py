"""Journal URLs, mounted at the site root."""

from django.urls import path

from journal import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("trades/", views.TradeListView.as_view(), name="trade_list"),
    path("trades/new/", views.TradeCreateView.as_view(), name="trade_create"),
    path("trades/import/", views.TradeImportView.as_view(), name="trade_import"),
    path("trades/export/", views.TradeExportView.as_view(), name="trade_export"),
    path("trades/<int:pk>/edit/", views.TradeUpdateView.as_view(), name="trade_update"),
    path("trades/<int:pk>/delete/", views.TradeDeleteView.as_view(), name="trade_delete"),
    path("accounts/new/", views.AccountCreateView.as_view(), name="account_create"),
]
