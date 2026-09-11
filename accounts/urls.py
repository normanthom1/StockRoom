from django.urls import include, path

from . import views

urlpatterns = [
    # Before the include, so it wins over the stock login view.
    path("login/", views.LoginView.as_view(), name="login"),
    path("signup/", views.signup, name="signup"),
    # logout (POST only), password_change and the password_reset flow.
    path("", include("django.contrib.auth.urls")),
]
