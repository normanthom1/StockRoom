from django.urls import include, path

from . import views

urlpatterns = [
    # Before the include, so it wins over the stock login view.
    path("login/", views.LoginView.as_view(), name="login"),
    path("demo-login/<str:who>/", views.demo_login, name="demo_login"),
    path("signup/", views.signup, name="signup"),
    path("invite/<str:token>/", views.invite_accept, name="invite_accept"),
    path("team/", views.team, name="team"),
    path("team/invite/", views.team_invite, name="team_invite"),
    path("team/<int:pk>/role/", views.team_role, name="team_role"),
    path("team/<int:pk>/active/", views.team_set_active, name="team_set_active"),
    path("team/<int:pk>/reset-link/", views.team_reset_link, name="team_reset_link"),
    # logout (POST only), password_change and the password_reset flow.
    path("", include("django.contrib.auth.urls")),
]
