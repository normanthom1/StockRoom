"""URL configuration for the stockroom project."""

from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", views.healthz, name="healthz"),
    path("accounts/", include("accounts.urls")),
    path("", include("stock.urls")),
]
