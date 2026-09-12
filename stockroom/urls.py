"""URL configuration for the stockroom project."""

from django.contrib import admin
from django.urls import include, path

from . import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", views.healthz, name="healthz"),
    path("sw.js", views.service_worker, name="service_worker"),
    path("offline/", views.offline, name="offline"),
    path("accounts/", include("accounts.urls")),
    path("", include("stock.urls")),
]
