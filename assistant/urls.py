from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("items/import/ai/", views.import_with_ai, name="import_with_ai"),
]
