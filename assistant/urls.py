from django.urls import path

from . import views

app_name = "assistant"

urlpatterns = [
    path("ask/", views.ask, name="ask"),
    path("ask/changes/<str:pid>/confirm/", views.confirm_change, name="confirm_change"),
    path("ask/changes/<str:pid>/cancel/", views.cancel_change, name="cancel_change"),
    path("ask/changes/<str:pid>/choose/<int:item>/", views.choose_item, name="choose_item"),
    path("ask/changes/<str:pid>/undo/", views.undo_change, name="undo_change"),
    path("items/import/ai/", views.import_with_ai, name="import_with_ai"),
    path("invoices/upload/", views.invoice_upload, name="invoice_upload"),
    path("invoices/batch/", views.invoice_batch_upload, name="invoice_batch_upload"),
    path("invoices/batch/<int:pk>/resume/", views.invoice_batch_resume, name="invoice_batch_resume"),
]
