from django.urls import path

from . import views

app_name = "stock"

urlpatterns = [
    path("", views.home, name="home"),
    path("item/<int:pk>/", views.item_detail, name="item_detail"),
    path("item/<int:pk>/count-sheet/", views.item_count_sheet, name="item_count_sheet"),
    path("item/<int:pk>/count/", views.item_count_save, name="item_count_save"),
    path("item/<int:pk>/price/", views.item_set_price, name="item_set_price"),
    path("item/<int:pk>/order-size/", views.item_set_order_size, name="item_set_order_size"),
    path("item/<int:pk>/toggle-reorder/", views.item_toggle_reorder, name="item_toggle_reorder"),
    path("log-usage/", views.log_usage, name="log_usage"),
    path("log-usage/<int:pk>/sheet/", views.log_usage_sheet, name="log_usage_sheet"),
    path("log-usage/<int:pk>/used-one/", views.log_used_one, name="log_used_one"),
    path("log-usage/<int:pk>/running-low/", views.log_running_low, name="log_running_low"),
    path("log-usage/<int:pk>/used-last/", views.log_used_last, name="log_used_last"),
    path("log-usage/undo/<int:pk>/", views.log_undo, name="log_undo"),
    path("reorder/", views.reorder_list, name="reorder_list"),
    path("deliveries/", views.deliveries, name="deliveries"),
    path("items/", views.items, name="items"),
    path("stocktake/", views.stocktake_step, name="stocktake_step"),
    path("stocktake/save/", views.stocktake_save, name="stocktake_save"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("suppliers/add/", views.supplier_add, name="supplier_add"),
    path("suppliers/<int:pk>/", views.supplier_row, name="supplier_row"),
    path("suppliers/<int:pk>/edit/", views.supplier_edit, name="supplier_edit"),
    path("suppliers/<int:pk>/update/", views.supplier_update, name="supplier_update"),
    path("suppliers/<int:pk>/lead-days/", views.supplier_lead_days, name="supplier_lead_days"),
    path("suppliers/<int:pk>/archive/", views.supplier_archive, name="supplier_archive"),
    path("suppliers/<int:pk>/unarchive/", views.supplier_unarchive, name="supplier_unarchive"),
    path("spending/", views.spending, name="spending"),
    path("demo/sheet/", views.demo_sheet, name="demo_sheet"),
    path("demo/toast/", views.demo_toast, name="demo_toast"),
    path("demo/undo/", views.demo_undo, name="demo_undo"),
]
