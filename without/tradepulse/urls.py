"""Root URL configuration for TradePulse."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("journal.auth_urls")),
    path("", include("journal.urls")),
]
