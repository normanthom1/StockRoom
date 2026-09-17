from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("items/import/ai/", views.import_with_ai, name="import_with_ai"),
    path("invoices/upload/", views.invoice_upload, name="invoice_upload"),
    path("invoices/batch/", views.invoice_batch_upload, name="invoice_batch_upload"),
    path("invoices/batch/<int:pk>/resume/", views.invoice_batch_resume, name="invoice_batch_resume"),
]
