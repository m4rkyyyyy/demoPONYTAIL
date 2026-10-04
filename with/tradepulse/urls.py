from django.contrib.auth import views as auth_views
from django.urls import include, path

from journal import views

urlpatterns = [
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("trades/", include("journal.urls")),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.SignUpView.as_view(), name="signup"),
]