from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.utils import timezone

from accounts.models import OrgOwned


class Supplier(OrgOwned):
    name = models.CharField(max_length=200)
    lead_days = models.PositiveSmallIntegerField(default=5, help_text="Calendar days from order to delivery.")
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                "organisation",
                Lower("name"),
                name="stock_supplier_name_unique_per_org",
                violation_error_message="You already have a supplier with that name.",
            ),
        ]

    def __str__(self):
        return self.name


class Item(OrgOwned):
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=50, help_text='Singular, e.g. "box".')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="items")
    # Per unit. Admins only: never render it on a page an assistant can open.
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Null means "about two weeks of usage", worked out by the forecast.
    order_size = models.PositiveIntegerField(null=True, blank=True)
    # Set by "add to reorder list", cleared when the item is ordered.
    pinned_to_reorder_at = models.DateTimeField(null=True, blank=True)
    barcode = models.CharField(max_length=64, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class StockEvent(OrgOwned):
    """Append-only log of what happened on the shelf. On-hand and forecasts are
    worked out from these; nothing is ever stored as a running total."""

    class Kind(models.TextChoices):
        COUNT = "count", "Counted"
        USED = "used", "Used"
        LOW = "low", "Running low"
        OUT = "out", "Out"
        RECEIVED = "received", "Received"

    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="events")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    # A default rather than auto_now_add, so offline taps replay at the time they happened.
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    kind = models.CharField(max_length=20, choices=Kind)
    qty = models.PositiveIntegerField(null=True, blank=True)
    # Sent by the device so a replayed offline tap can't be logged twice.
    client_id = models.UUIDField(unique=True, null=True, blank=True)

    class Meta:
        # qty >= 0 comes from PositiveIntegerField, which adds its own DB check.
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind__in=["count", "used", "received"]) | Q(qty__isnull=False),
                name="stock_event_qty_required",
                violation_error_message="Counts, uses and deliveries need a quantity.",
            ),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.qty if self.qty is not None else ''} {self.item}".strip()


class OrderLine(OrgOwned):
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name="order_lines")
    qty = models.PositiveIntegerField()
    # The item's price when it was ordered, so later price changes don't rewrite history.
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ordered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    ordered_at = models.DateTimeField(default=timezone.now)
    expected_at = models.DateTimeField(blank=True)
    received_qty = models.PositiveIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.qty} × {self.item}"

    @property
    def is_open(self):
        return self.received_at is None and self.cancelled_at is None

    def save(self, *args, **kwargs):
        if self.expected_at is None:
            self.expected_at = self.ordered_at + timedelta(days=self.item.supplier.lead_days)
        super().save(*args, **kwargs)
