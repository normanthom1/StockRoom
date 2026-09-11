from django.contrib import admin

from .models import Item, OrderLine, StockEvent, Supplier

# Platform staff debugging only; see accounts/admin.py.


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "lead_days")
    list_filter = ("organisation",)
    search_fields = ("name",)


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ("name", "organisation", "supplier", "unit", "price", "is_active")
    list_filter = ("organisation", "is_active")
    search_fields = ("name", "barcode")


@admin.register(StockEvent)
class StockEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "item", "kind", "qty", "user", "organisation")
    list_filter = ("organisation", "kind")
    search_fields = ("item__name",)
    date_hierarchy = "created_at"


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = ("ordered_at", "item", "qty", "expected_at", "received_at", "cancelled_at", "organisation")
    list_filter = ("organisation",)
    search_fields = ("item__name",)
