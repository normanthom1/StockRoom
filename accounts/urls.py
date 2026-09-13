from django.urls import include, path

from . import views

urlpatterns = [
    # Before the include, so it wins over the stock login view.
    path("login/", views.LoginView.as_view(), name="login"),
    path("demo-login/", views.demo_login, name="demo_login"),
    path("signup/", views.signup, name="signup"),
    path("code/", views.enter_code, name="enter_code"),
    path("team/", views.team, name="team"),
    path("team/add/", views.team_add, name="team_add"),
    path("team/<int:pk>/role/", views.team_role, name="team_role"),
    path("team/<int:pk>/active/", views.team_set_active, name="team_set_active"),
    # logout (POST only), password_change and the password_reset flow.
    path("", include("django.contrib.auth.urls")),
]
