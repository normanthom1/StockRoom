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
    # {"key": normalised name, "values": [...]}: Gemini's embedding of the name, for
    # matching (stock/matching.py). Rebuilt when the name no longer normalises to key.
    name_embedding = models.JSONField(null=True, blank=True, editable=False)
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

    @property
    def over_delivered(self):
        return self.received_qty is not None and self.received_qty > self.qty

    def save(self, *args, **kwargs):
        if self.supplier_id is None:
            self.supplier = self.item.supplier
        if self.expected_at is None:
            self.expected_at = self.ordered_at + timedelta(days=self.supplier.lead_days)
        super().save(*args, **kwargs)

    def receive(self, qty, user, unit_price=None):
        """Receive this open line, from Deliveries or an invoice. Less than was
        ordered splits the rest off into a new open line, so it stays tracked
        as back-ordered. More is an over-delivery: all of it is received and
        the line closes with received_qty above qty."""
        remainder = self.qty - qty
        self.received_qty = qty
        self.received_at = timezone.now()
        self.received_by = user
        if unit_price is not None:
            self.unit_price = unit_price
            self.item.price = unit_price
            self.item.save(update_fields=["price"])
        self.save()
        StockEvent.objects.create(organisation=self.organisation, item=self.item, user=user, kind="received", qty=qty)
        if remainder > 0:
            OrderLine.objects.create(
                organisation=self.organisation,
                item=self.item,
                supplier=self.supplier,
                qty=remainder,
                unit_price=self.unit_price,
                ordered_by=self.ordered_by,
                ordered_at=self.ordered_at,
                expected_at=self.expected_at,
            )


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
    # The supplier's own number for it, which a re-scan or re-export keeps.
    invoice_number = models.CharField(max_length=64, blank=True)
    # SHA-256 of the file it was read from, and the file's name.
    checksum = models.CharField(max_length=64, blank=True)
    source_name = models.CharField(max_length=255, blank=True)
    # Every line's total is within 1c of qty x unit price.
    totals_ok = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status, default=Status.PARSED)
    created_at = models.DateTimeField(default=timezone.now)
    # A repeat a manager imported anyway, and why.
    forced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    force_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        # An invoice counts once: a repeat is saved as ignored, or forced. The
        # database holds the line, so two uploads at once can't both get in.
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "checksum"],
                condition=~Q(checksum="") & ~Q(status="ignored") & Q(forced_by__isnull=True),
                name="stock_invoice_file_once",
            ),
            models.UniqueConstraint(
                "organisation",
                "supplier",
                Lower("invoice_number"),
                condition=Q(supplier__isnull=False) & ~Q(invoice_number="") & ~Q(status="ignored")
                & Q(forced_by__isnull=True),
                name="stock_invoice_number_once_per_supplier",
            ),
        ]

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
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines")

    def __str__(self):
        return f"{self.qty} × {self.description or self.sku}"


class ItemAlias(OrgOwned):
    """A name that turned out to be one of the practice's items, so the next
    invoice or import matches it straight away (stock/matching.py). Undo sets
    reverted_at and nothing is deleted, so this is also the merge history."""

    class Method(models.TextChoices):
        SKU = "sku", "Same supplier code"
        ALIAS = "alias", "Matched before"
        EXACT = "exact", "Same name"
        SYNONYM = "synonym", "Another name for it"
        FUZZY = "fuzzy", "Close spelling"
        SEMANTIC = "semantic", "Similar meaning"
        MANUAL = "manual", "Picked by hand"

    class Source(models.TextChoices):
        INVOICE = "invoice", "Invoice"
        IMPORT = "import", "Import"
        MANUAL = "manual", "By hand"

    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name="aliases")
    key = models.CharField(max_length=200)
    raw_name = models.CharField(max_length=200)
    method = models.CharField(max_length=20, choices=Method)
    confidence = models.FloatField()
    source = models.CharField(max_length=20, choices=Source)
    source_invoice = models.ForeignKey(Invoice, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    reverted_at = models.DateTimeField(null=True, blank=True)
    reverted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organisation", "key"], condition=Q(reverted_at__isnull=True), name="stock_itemalias_live_key_unique"
            ),
        ]

    def __str__(self):
        return f"{self.raw_name} → {self.item}"


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
