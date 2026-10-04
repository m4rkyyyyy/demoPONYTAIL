"""Authentication routes, mounted under ``/accounts/`` by the root URLconf.

Django's built-in auth views are used directly -- the only customisation is
the login template. Logout is POST-only since Django 4.1, so ``base.html``
wraps the nav link in a small form.
"""

from django.contrib.auth.views import LoginView, LogoutView
from django.urls import path

from journal.views import SignUpView

urlpatterns = [
    path(
        "login/",
        LoginView.as_view(template_name="registration/login.html", redirect_authenticated_user=True),
        name="login",
    ),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("signup/", SignUpView.as_view(), name="signup"),
]
