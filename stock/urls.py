from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.home, name="home"),
    path("item/<int:pk>/", views.item_detail, name="item_detail"),
    path("log-usage/", views.log_usage, name="log_usage"),
    path("log-usage/<int:pk>/sheet/", views.log_usage_sheet, name="log_usage_sheet"),
    path("log-usage/<int:pk>/used-one/", views.log_used_one, name="log_used_one"),
    path("log-usage/<int:pk>/running-low/", views.log_running_low, name="log_running_low"),
    path("log-usage/<int:pk>/used-last/", views.log_used_last, name="log_used_last"),
    path("log-usage/undo/<int:pk>/", views.log_undo, name="log_undo"),
    path("reorder/", views.reorder_list, name="reorder_list"),
    path("deliveries/", views.deliveries, name="deliveries"),
    path("items/", views.items, name="items"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("spending/", views.spending, name="spending"),
    path("demo/sheet/", views.demo_sheet, name="demo_sheet"),
    path("demo/toast/", views.demo_toast, name="demo_toast"),
    path("demo/undo/", views.demo_undo, name="demo_undo"),
]
