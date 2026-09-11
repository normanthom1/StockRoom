from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.home, name="home"),
    path("item/<int:pk>/", views.item_detail, name="item_detail"),
    path("log-usage/", views.log_usage, name="log_usage"),
    path("reorder/", views.reorder_list, name="reorder_list"),
    path("deliveries/", views.deliveries, name="deliveries"),
    path("items/", views.items, name="items"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("spending/", views.spending, name="spending"),
    path("demo/sheet/", views.demo_sheet, name="demo_sheet"),
    path("demo/toast/", views.demo_toast, name="demo_toast"),
    path("demo/undo/", views.demo_undo, name="demo_undo"),
]
