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
    is_active = models.BooleanField(default=True)

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
    # The preferred supplier: the reorder list groups by it and orders go to it.
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="items")
    # Others that sell it, for when the preferred one can't. Switching makes
    # one of these the preferred supplier and keeps the old one here.
    other_suppliers = models.ManyToManyField(Supplier, blank=True, related_name="backup_items")
    # Per unit. Admins only: never render it on a page an assistant can open.
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # Null means "about two weeks of usage", worked out by the forecast.
    order_size = models.PositiveIntegerField(null=True, blank=True)
    # Set by "add to reorder list", cleared when the item is ordered.
    pinned_to_reorder_at = models.DateTimeField(null=True, blank=True)
    # An assistant asking for it to go on the reorder list; a manager adds it
    # or turns it down. Cleared when the item is ordered.
    reorder_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    barcode = models.CharField(max_length=64, blank=True)
    # The preferred supplier's product code, which is how an invoice line finds its item.
    supplier_sku = models.CharField(max_length=64, blank=True)
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
    # Who it was ordered from, which stays put if the item's preferred supplier changes later.
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="order_lines")
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
    # The practice's order number as the supplier quotes it back on the invoice.
    order_ref = models.CharField(max_length=64, blank=True)

    def __str__(self):
        return f"{self.qty} × {self.item}"

    @property
    def is_open(self):
        return self.received_at is None and self.cancelled_at is None

    def save(self, *args, **kwargs):
        if self.supplier_id is None:
            self.supplier = self.item.supplier
        if self.expected_at is None:
            self.expected_at = self.ordered_at + timedelta(days=self.supplier.lead_days)
        super().save(*args, **kwargs)


class Invoice(OrgOwned):
    """A supplier invoice read into lines (stock/invoices.py). Only the parsed
    lines are kept, never the file. Admins only: it carries prices."""

    class Status(models.TextChoices):
        PARSED = "parsed", "Ready to check"
        COMPLETE = "complete", "Received"
        PARTIAL = "partial", "Partly received"
        IGNORED = "ignored", "Already imported"
        CONFLICT = "conflict", "Needs checking"

    # As written on the invoice; supplier is set when it matches one of the practice's.
    supplier_name = models.CharField(max_length=200, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices")
    issued_on = models.DateField(null=True, blank=True)
    order_ref = models.CharField(max_length=64, blank=True)
    # Every line's total is within 1c of qty x unit price.
    totals_ok = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status, default=Status.PARSED)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.supplier_name or 'Invoice'} {self.issued_on or ''}".strip()


class InvoiceLine(OrgOwned):
    """A product line. Freight, GST, discounts, subtotals and totals are dropped when parsing."""

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    sku = models.CharField(max_length=64, blank=True)
    description = models.CharField(max_length=200, blank=True)
    # Null when the invoice didn't show it, which fails the totals check.
    qty = models.PositiveIntegerField(null=True, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    line_total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    def __str__(self):
        return f"{self.qty} × {self.description or self.sku}"


# The shared catalogue: the same reference list for every practice, so not
# OrgOwned. Practices copy products from it into their own Items. Loaded from
# stock/catalogue_data.py by stock.catalogue.sync_catalogue.


class CatalogueSupplier(models.Model):
    name = models.CharField(max_length=200, unique=True)
    website = models.URLField(blank=True)

    def __str__(self):
        return self.name


class CatalogueProduct(models.Model):
    name = models.CharField(max_length=200, unique=True)
    unit = models.CharField(max_length=50)
    category = models.CharField(max_length=100)
    position = models.PositiveIntegerField(help_text="Display order: by category, then as listed.")
    suppliers = models.ManyToManyField(CatalogueSupplier, through="CatalogueOffer", related_name="products")

    def __str__(self):
        return self.name


class CatalogueOffer(models.Model):
    """This supplier sells that product. A product's first supplier is the
    default pick for a practice that uses none of them yet."""

    product = models.ForeignKey(CatalogueProduct, on_delete=models.CASCADE, related_name="offers")
    supplier = models.ForeignKey(CatalogueSupplier, on_delete=models.CASCADE, related_name="offers")
    position = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["position"]
        constraints = [models.UniqueConstraint(fields=["product", "supplier"], name="stock_catalogue_offer_unique")]


class DemoResetState(models.Model):
    """Singleton row (pk=1): the NZT date the public demo was last reset.
    See stockroom.demo.DemoResetMiddleware. Not org-owned - it tracks the
    platform's demo mode, not any one practice's data."""

    date = models.DateField()
