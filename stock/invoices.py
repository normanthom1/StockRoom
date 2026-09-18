"""Reading a supplier invoice (PDF, photo or CSV) into Invoice and InvoiceLine rows.

Photos and PDFs go through Gemini; a CSV is read directly. Both come out in
the same shape, keyed by MAPPING, so the checks below run once. Only the
parsed lines are saved here; the file itself is kept as an InvoiceDocument,
because a GST-registered practice has to hold the original for seven years
(see stock/nztax.py). ingest() is the one way a read invoice is saved, and
where repeats and conflicts are settled.
"""

import csv
import hashlib
import io
import logging
import re
import subprocess
import sys
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from assistant import gemini
from stockroom.analytics import track

from . import nztax
from .matching import Matcher, record_merge
from .models import (
    Invoice,
    InvoiceBatchFile,
    InvoiceDocument,
    InvoiceLine,
    ItemAlias,
    OrderLine,
    StockEvent,
    Supplier,
)

logger = logging.getLogger(__name__)

# Invoice field name -> ours. A CSV can use either name as its header.
MAPPING = {
    "invoice_date": "date",
    "vendor": "supplier",
    "po_number": "order_id",
    "invoice_number": "invoice_number",
    "sku": "sku",
    "description": "description",
    "qty": "qty",
    "unit": "unit_price",
    "line_total": "line_total",
    "kind": "kind",
    "gst_number": "gst_number",
    "total_excl_gst": "total_excl_gst",
    "gst_amount": "gst_amount",
    "total_incl_gst": "total_incl_gst",
}
HEADER_FIELDS = ("date", "supplier", "order_id", "invoice_number",
                 "gst_number", "total_excl_gst", "gst_amount", "total_incl_gst")
CSV_TYPES = ("text/csv", "application/vnd.ms-excel")  # Excel on Windows labels CSVs as the latter
ONE_CENT = Decimal("0.01")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")

# What a line is. Gemini labels every line it reads and a CSV can carry a
# `kind` column, so the extras are dropped by what they are rather than by
# what they happen to be called.
PRODUCT = "product"
LINE_KINDS = (PRODUCT, "freight", "gst", "discount", "subtotal", "total", "other")

# The fallback, for a CSV with no `kind` column and for a line a reading left
# unlabelled. It matches the code, or the description when there's no code, so
# it drops a codeless "Total Etch" and keeps freight that carries a product
# code. Those two are exactly what `kind` above exists to get right.
NOT_A_PRODUCT = re.compile(r"\s*(sub[- ]?total|total|gst|freight|shipping|courier|discount|rounding)\b", re.IGNORECASE)


def is_product(kind, sku, description):
    """Whether a line is something supplied, and so worth keeping. A kind we
    recognise decides on its own; anything else falls back to the word match."""
    kind = str(kind or "").strip().lower()
    if kind in LINE_KINDS:
        return kind == PRODUCT
    return not NOT_A_PRODUCT.match(sku or description)


def _gst_number(raw):
    """The supplier's GST number, kept only when IRD's check digit says it's a
    real one. A misread digit is worse than a blank: blank is visibly missing,
    but a wrong number looks like it was checked when it wasn't."""
    digits = nztax.normalise_gst_number(raw)
    return digits if nztax.is_valid_gst_number(digits) else ""


INVOICE_RULES = """You read a supplier invoice for a New Zealand dental practice into its details and product lines.
- invoice_date: the date it was issued, as YYYY-MM-DD. New Zealand writes dates day first.
- vendor: the supplier that sent it. If it's one of this practice's suppliers, use that name exactly as written here: {suppliers}.
- po_number: the practice's order or purchase order number, if shown.
- invoice_number: the supplier's own number for this invoice, if shown. Not the order number.
- gst_number: the supplier's GST number, if shown. Digits only, no dashes. It is the supplier's, never the practice's.
- total_excl_gst, gst_amount, total_incl_gst: the invoice's own totals, exactly as printed. Leave out any the invoice doesn't show; never work one out from the others.
- lines: every line the invoice charges for, in order, including freight, GST, discounts, subtotals and totals.
- kind: what the line is. "product" for something supplied, otherwise "freight", "gst", "discount", "subtotal", "total" or "other". Judge it by what the line is for, not by its wording: "Total Etch" is a product, and a freight charge is freight even when it carries a product code.
- sku: the supplier's product code. description: the product as written.
- qty: how many were supplied. unit: the price for one. line_total: the line's amount.
- Copy numbers exactly as printed, without currency symbols. Never work one out or invent it; leave it out instead.
"""

INVOICE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "invoice_date": {"type": "STRING"},
        "vendor": {"type": "STRING"},
        "po_number": {"type": "STRING"},
        "invoice_number": {"type": "STRING"},
        "gst_number": {"type": "STRING"},
        "total_excl_gst": {"type": "NUMBER", "nullable": True},
        "gst_amount": {"type": "NUMBER", "nullable": True},
        "total_incl_gst": {"type": "NUMBER", "nullable": True},
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "kind": {"type": "STRING", "enum": list(LINE_KINDS)},
                    "sku": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "qty": {"type": "NUMBER", "nullable": True},
                    "unit": {"type": "NUMBER", "nullable": True},
                    "line_total": {"type": "NUMBER", "nullable": True},
                },
                "required": ["description"],
            },
        },
    },
    "required": ["lines"],
}


def parse_invoice(organisation, data, mime_type, source_name=""):
    """Read an invoice file into an unsaved Invoice and its product lines, as
    parsed when it passes check_invoice, otherwise as a conflict. ingest saves
    them. A file read before gets that reading instead of another Gemini call.
    Raises gemini.GeminiError if a photo or PDF can't be read, and ValueError
    for any other kind of file."""
    if not (mime_type in CSV_TYPES or mime_type == "application/pdf" or mime_type.startswith("image/")):
        raise ValueError(f"Can't read an invoice from {mime_type}.")
    checksum = hashlib.sha256(data).hexdigest()
    if earlier := _imported(organisation).filter(checksum=checksum).order_by("created_at").first():
        invoice, lines = _copy(earlier)
    elif mime_type in CSV_TYPES:
        invoice, lines = _build(organisation, *_read_csv(data))
    else:
        invoice, lines = _build(organisation, *_read_with_ai(organisation, data, mime_type))
    invoice.checksum, invoice.source_name = checksum, source_name[:255]
    return invoice, lines


def _imported(organisation):
    return Invoice.objects.for_org(organisation).exclude(status=Invoice.Status.IGNORED)


def _copy(earlier):
    fields = ("supplier_name", "supplier", "issued_on", "order_ref", "invoice_number",
              "supplier_gst_number", "total_excl_gst", "gst_amount", "total_incl_gst")
    invoice = Invoice(organisation=earlier.organisation, **{name: getattr(earlier, name) for name in fields})
    lines = [
        InvoiceLine(organisation=line.organisation, sku=line.sku, description=line.description, qty=line.qty,
                    unit_price=line.unit_price, line_total=line.line_total)
        for line in earlier.lines.order_by("pk")
    ]
    check_invoice(invoice, lines)
    return invoice, lines


def _read_with_ai(organisation, data, mime_type):
    suppliers = Supplier.objects.for_org(organisation).filter(is_active=True).order_by("name")
    rules = INVOICE_RULES.format(suppliers=", ".join(s.name for s in suppliers) or "(none set up yet)")
    reply = gemini.generate(rules, [("user", "Read the attached invoice.")], schema=INVOICE_SCHEMA,
                            attachment=(mime_type, data))
    if not isinstance(reply, dict):
        reply = {}
    header = {MAPPING[key]: reply.get(key) for key in (
        "invoice_date", "vendor", "po_number", "invoice_number",
        "gst_number", "total_excl_gst", "gst_amount", "total_incl_gst",
    )}
    lines = [_rename(line) for line in reply.get("lines") or [] if isinstance(line, dict)]
    return header, lines


def _read_csv(data):
    """One row per line; the date, supplier and order columns can repeat on
    every row or appear on just the first."""
    rows = [_rename(row) for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig", errors="replace")))]
    header = {name: next((row[name] for row in rows if row.get(name)), None) for name in HEADER_FIELDS}
    return header, rows


def _rename(raw):
    return {MAPPING.get(key.strip().lower(), key.strip().lower()): value for key, value in raw.items() if key}


def _build(organisation, header, raw_lines):
    """The unsaved Invoice and InvoiceLines a preview shows and save_invoice persists."""
    lines = []
    for raw in raw_lines:
        sku, description = _text(raw.get("sku"), 64), _text(raw.get("description"), 200)
        if not (sku or description) or not is_product(raw.get("kind"), sku, description):
            continue
        qty, unit_price, line_total = _number(raw.get("qty")), _number(raw.get("unit_price")), _number(raw.get("line_total"))
        if qty is not None:
            qty = int(qty) if qty == qty.to_integral_value() and qty >= 0 else None
        lines.append(InvoiceLine(organisation=organisation, sku=sku, description=description, qty=qty,
                                 unit_price=unit_price, line_total=line_total))

    supplier_name = _text(header.get("supplier"), 200)
    invoice = Invoice(
        organisation=organisation,
        supplier_name=supplier_name,
        supplier=Supplier.objects.for_org(organisation).filter(name__iexact=supplier_name).first() if supplier_name else None,
        issued_on=_date(header.get("date")),
        order_ref=_text(header.get("order_id"), 64),
        invoice_number=_text(header.get("invoice_number"), 64),
        supplier_gst_number=_gst_number(header.get("gst_number")),
        total_excl_gst=cents(_number(header.get("total_excl_gst"))),
        gst_amount=cents(_number(header.get("gst_amount"))),
        total_incl_gst=cents(_number(header.get("total_incl_gst"))),
    )
    check_invoice(invoice, lines)
    for line in lines:  # checked above at full precision; stored to the cent
        line.unit_price = cents(line.unit_price)
        line.line_total = cents(line.line_total)
    return invoice, lines


def adds_up(line):
    """It has a qty and unit price, and the line total, if the invoice shows one, is their product to the cent."""
    return None not in (line.qty, line.unit_price) and (
        line.line_total is None or abs(line.line_total - line.qty * line.unit_price) <= ONE_CENT)


def check_invoice(invoice, lines):
    """The conflicts that block a whole invoice: no supplier of the practice's,
    or a line whose total isn't qty x unit price to the cent. Either lands it as
    a conflict for someone to check. Sets totals_ok and status."""
    invoice.totals_ok = bool(lines) and all(adds_up(line) for line in lines)
    invoice.status = Invoice.Status.PARSED if invoice.totals_ok and invoice.supplier_id else Invoice.Status.CONFLICT


def match_lines(invoice, lines):
    """Suggest an item for every line (line.match) and apply the sure ones. A
    line still without an item after this, or after a manager checks it, is
    an unknown product: it's saved unmatched, for someone to match by hand."""
    matches = Matcher(invoice.organisation).match_all([
        {"name": line.description or line.sku, "sku": line.sku, "supplier": invoice.supplier, "price": line.unit_price}
        for line in lines
    ])
    for line, match in zip(lines, matches, strict=True):
        line.match = match
        if match.band == "sure":
            line.item = match.item


def match_orders(invoice, lines):
    """Pick the open order each line was delivered against (line.order), from
    the invoice supplier's open order lines: those on the invoice's own order
    number first, by the supplier's product code, then the item the line
    matched, then its name among just the items on order. A line given an
    order already keeps it, and no order is picked for two lines."""
    for line in lines:
        line.order = getattr(line, "order", None)
    open_orders = [] if invoice.supplier_id is None else sorted(
        OrderLine.objects.for_org(invoice.organisation)
        .filter(supplier=invoice.supplier, received_at=None, cancelled_at=None).select_related("item"),
        key=lambda order: (order.order_ref.lower() != invoice.order_ref.lower() or not invoice.order_ref,
                           order.expected_at, order.pk),
    )
    if not open_orders:
        return
    taken = {line.order.pk for line in lines if line.order}
    on_order = Matcher(invoice.organisation, items=list({order.item_id: order.item for order in open_orders}.values()))
    for line in lines:
        if line.order:
            continue
        free = [order for order in open_orders if order.pk not in taken]
        item = line.item
        if item is None:
            match = on_order.match(line.description or line.sku, sku=line.sku, supplier=invoice.supplier)
            item = match.item if match.band == "sure" else None
        sku = line.sku.lower()
        line.order = (next((order for order in free if sku and order.item.supplier_sku.lower() == sku), None)
                      or next((order for order in free if order.item == item), None))
        if line.order:
            taken.add(line.order.pk)


PRICE_RISE_THRESHOLD = Decimal("1.10")


def price_needs_confirming(current_price, new_price):
    """True when a new unit price is a rise of more than 10% on the item's price now."""
    return current_price is not None and new_price is not None and new_price > current_price * PRICE_RISE_THRESHOLD


# The same window the rest of the app gives an undo (stock/views.py UNDO_WINDOW).
# It used to be 30 seconds, which nobody could reach: the toast carrying the
# Undo button hides itself after 6, and there was no other way to it. Confirming
# an invoice receives stock and overwrites item prices, so it needs at least as
# long to undo as marking one thing ordered does.
UNDO_WINDOW = timedelta(minutes=10)


def receive(invoice, lines, user):
    """Receive each of a saved invoice's lines against its order (line.order),
    the same way a tap on Deliveries does, if the order's still open. The
    supplier's product code goes onto the item, so it matches first time next
    month. Then the invoice is received, partly received or nothing received yet.

    A line's unit price flows onto the item, unless it's a rise of more than
    10% that the preview wasn't ticked to confirm (line.price_confirmed).

    Records what changed on the invoice (undo_snapshot), so undo_ingest can put
    it all back for a short window after - stock events, order status, price."""
    match_orders(invoice, lines)
    with transaction.atomic():
        snapshot = list(invoice.undo_snapshot or [])
        for line in lines:
            if line.order_line_id or not (line.order and line.qty):
                continue
            order = (OrderLine.objects.select_for_update().select_related("item")
                     .filter(pk=line.order.pk, organisation=invoice.organisation, supplier=invoice.supplier,
                             received_at=None, cancelled_at=None).first())
            if order is None:
                continue
            unit_price_before, item_price_before, item_before_id = order.unit_price, order.item.price, line.item_id
            update_item_price = (getattr(line, "price_confirmed", False)
                                 or not price_needs_confirming(order.item.price, line.unit_price))
            event, remainder = order.receive(line.qty, user, line.unit_price, update_item_price=update_item_price)
            line.order_line, line.item = order, order.item
            snapshot.append({
                "order_line_id": order.pk,
                "invoice_line_id": line.pk,
                "unit_price": str(unit_price_before) if unit_price_before is not None else None,
                "item_price": str(item_price_before) if item_price_before is not None else None,
                "item_before_id": item_before_id,
                "stock_event_id": event.pk,
                "remainder_id": remainder.pk if remainder else None,
            })
            if line.sku and not order.item.supplier_sku and order.item.supplier_id == invoice.supplier_id:
                order.item.supplier_sku = line.sku
                order.item.save(update_fields=["supplier_sku"])
        InvoiceLine.objects.bulk_update(lines, ["order_line", "item"])
        _finish(invoice, snapshot)


def receive_to_stock(invoice, line, user):
    """Put a line that isn't on any order straight onto the shelf: a receipt, or
    a delivery someone ordered outside StockRoom. The invoice's price becomes the
    item's price, and Undo puts both back. The caller checks the line has an item
    and a quantity and hasn't been received already."""
    with transaction.atomic():
        item_price_before = line.item.price
        if line.unit_price is not None and line.item.price != line.unit_price:
            line.item.price = line.unit_price
            line.item.save(update_fields=["price"])
        event = StockEvent.objects.create(organisation=invoice.organisation, item=line.item,
                                          user=user, kind="received", qty=line.qty)
        line.stock_event = event
        line.save(update_fields=["stock_event"])
        _finish(invoice, [*(invoice.undo_snapshot or []), {
            "order_line_id": None,
            "invoice_line_id": line.pk,
            "unit_price": None,
            "item_price": str(item_price_before) if item_price_before is not None else None,
            "item_before_id": line.item_id,
            "stock_event_id": event.pk,
            "remainder_id": None,
        }])


def _finish(invoice, snapshot):
    """Set how much of the invoice has landed, and open the undo window."""
    received = invoice.lines.filter(Q(order_line__isnull=False) | Q(stock_event__isnull=False)).count()
    total = invoice.lines.count()
    invoice.status = (Invoice.Status.COMPLETE if received == total
                      else Invoice.Status.PARTIAL if received else Invoice.Status.PARSED)
    invoice.undo_snapshot = snapshot
    invoice.undo_until = timezone.now() + UNDO_WINDOW
    invoice.save(update_fields=["status", "undo_snapshot", "undo_until"])


def undo_ingest(invoice):
    """Put back everything receive() changed for this invoice: delete the stock
    events and any back-order split it made, and restore each order line and
    item price to what they were just before. Only valid while undo_until
    hasn't passed - the view checks that."""
    snapshot = invoice.undo_snapshot or []
    with transaction.atomic():
        orders = {
            order.pk: order for order in
            OrderLine.objects.select_for_update().select_related("item")
            .filter(pk__in=[entry["order_line_id"] for entry in snapshot if entry["order_line_id"]])
        }
        for entry in snapshot:
            line = InvoiceLine.objects.select_related("item").filter(pk=entry["invoice_line_id"]).first()
            order = orders.get(entry["order_line_id"])
            if order is None and not (entry["order_line_id"] is None and line):
                continue
            if entry["remainder_id"]:
                OrderLine.objects.filter(pk=entry["remainder_id"]).delete()
            InvoiceLine.objects.filter(pk=entry["invoice_line_id"]).update(
                order_line=None, stock_event=None, item_id=entry["item_before_id"]
            )
            StockEvent.objects.filter(pk=entry["stock_event_id"]).delete()
            if order is not None:
                order.received_qty = order.received_at = order.received_by = order.price_before = None
                order.unit_price = Decimal(entry["unit_price"]) if entry["unit_price"] is not None else None
                order.save()
            item = order.item if order is not None else line.item
            item_price = Decimal(entry["item_price"]) if entry["item_price"] is not None else None
            if item is not None and item.price != item_price:
                item.price = item_price
                item.save(update_fields=["price"])
        invoice.status = Invoice.Status.PARSED
        invoice.undo_snapshot, invoice.undo_until = None, None
        invoice.save(update_fields=["status", "undo_snapshot", "undo_until"])


def find_original(invoice):
    """The earlier invoice this one repeats: the same file, or the same number from the same supplier."""
    same = Q(checksum=invoice.checksum) if invoice.checksum else Q(pk__in=[])
    if invoice.supplier_id and invoice.invoice_number:
        same |= Q(supplier_id=invoice.supplier_id, invoice_number__iexact=invoice.invoice_number)
    return _imported(invoice.organisation).filter(same).exclude(pk=invoice.pk).order_by("created_at").first()


def ingest(invoice, lines, user, force_reason=""):
    """Save a read invoice, from an upload or a batch. A repeat of one imported
    before is saved as ignored and changes nothing, unless a manager imports it
    anyway (force_reason). Lines matched this time (line.match) are remembered
    for next time. An invoice checked again (one with a pk) has its lines replaced."""
    if force_reason:
        invoice.forced_by, invoice.force_reason = user, force_reason
    check_invoice(invoice, lines)
    if not invoice.forced_by_id and find_original(invoice):
        return _ignore(invoice, lines)
    try:
        with transaction.atomic():
            save_invoice(invoice, lines)
            for line in lines:
                match = getattr(line, "match", None)
                if line.item_id and match and match.item == line.item:
                    record_merge(match, name=line.description or line.sku, user=user,
                                 source=ItemAlias.Source.INVOICE, invoice=invoice)
            if invoice.status == Invoice.Status.PARSED:
                receive(invoice, lines, user)
    except IntegrityError:
        # Another upload of it got in between find_original and here.
        if invoice.forced_by_id or not find_original(invoice):
            raise
        return _ignore(invoice, lines)
    _track_ingest(invoice, lines)
    return invoice


def _track_ingest(invoice, lines):
    received = sum(1 for line in lines if line.order_line_id or line.stock_event_id)
    track("invoice_imported", org=invoice.organisation_id, status=invoice.status,
          lines=len(lines), received=received, forced=bool(invoice.forced_by_id))
    if invoice.status == Invoice.Status.CONFLICT:
        # The one that leaves the practice's counts wrong until someone acts.
        track("invoice_needs_checking", org=invoice.organisation_id,
              no_supplier=not invoice.supplier_id, totals_ok=invoice.totals_ok)


def _ignore(invoice, lines):
    invoice.status = Invoice.Status.IGNORED
    save_invoice(invoice, lines)
    logger.info("Ignored a repeat invoice for organisation %s: %r, sha256 %s",
                invoice.organisation_id, invoice.source_name, invoice.checksum)
    return invoice


TO_READ = [InvoiceBatchFile.State.PENDING, InvoiceBatchFile.State.FAILED]
FILE_STATE = {Invoice.Status.IGNORED: InvoiceBatchFile.State.IGNORED, Invoice.Status.CONFLICT: InvoiceBatchFile.State.PARSED}


def start_batch(batch):
    """Run a batch in its own process, so it outlives the request.
    ponytail: a process per run, not a worker queue. A redeploy stops it part
    way, and Resume carries on; add a worker if batches outgrow that."""
    subprocess.Popen([sys.executable, "manage.py", "import_invoices", str(batch.pk)], cwd=settings.BASE_DIR)


def run_batch(batch):
    """Read and ingest a batch's files that are waiting or failed, the same way
    as an upload. Each file is read, matched and saved in one transaction, so a
    run killed part way leaves every file either done or still to do. Two runs
    at once share the files: one being read is locked, and the other skips it."""
    tried = []
    while True:
        with transaction.atomic():
            file = (InvoiceBatchFile.objects.select_for_update(skip_locked=True)
                    .filter(batch=batch, state__in=TO_READ).exclude(pk__in=tried).order_by("pk").first())
            if file is None:
                return
            tried.append(file.pk)
            try:
                with transaction.atomic():
                    data = bytes(file.data)
                    # Kept before the batch file's own copy is cleared below, so
                    # the original survives as the record behind the lines.
                    keep_document(batch.organisation, batch.created_by, file.name, file.content_type, data)
                    invoice, lines = parse_invoice(batch.organisation, data, file.content_type, file.name)
                    match_lines(invoice, lines)
                    invoice = ingest(invoice, lines, batch.created_by)
            except Exception as error:  # one unreadable file mustn't stop the rest
                logger.exception("Couldn't read %r in invoice batch %s", file.name, batch.pk)
                file.state, file.error = InvoiceBatchFile.State.FAILED, (str(error) or type(error).__name__)[:200]
            else:
                file.state = FILE_STATE.get(invoice.status, InvoiceBatchFile.State.INGESTED)
                file.invoice, file.data, file.error = invoice, None, ""
            file.save()


def batch_summary(batch, files):
    """What a finished batch actually did to the practice's records.

    A batch has no preview: each file is read and ingested as it arrives. Until
    now the only feedback was a state chip per file, which says a file was read
    but not what changed because of it. This is the review step, after the fact.

    The prices are the part worth reading. receive() quietly declines to apply a
    unit price that's a rise of more than PRICE_RISE_THRESHOLD on what the item
    costs now, because an invoice read off a photo is a likelier explanation
    than a 3x price rise. Nothing said so, so a held price was invisible: the
    manager believed the import had priced the item, and it hadn't. They're
    listed here with the choice the preview would have offered.
    """
    invoice_ids = [file.invoice_id for file in files if file.invoice_id]
    invoices = {inv.pk: inv for inv in Invoice.objects.filter(pk__in=invoice_ids)}
    lines = (InvoiceLine.objects.filter(invoice_id__in=invoice_ids)
             .select_related("item", "invoice").order_by("pk"))

    received = [line for line in lines if line.order_line_id or line.stock_event_id]
    # Newest line per item wins: two invoices for the same product in one batch
    # is one decision, not two.
    held = {}
    for line in received:
        if line.item and price_needs_confirming(line.item.price, line.unit_price):
            held[line.item_id] = line

    undo_until = max((inv.undo_until for inv in invoices.values() if inv.undo_snapshot), default=None)
    return {
        "invoices": len(invoice_ids),
        "received_lines": len(received),
        "received_units": sum(line.qty or 0 for line in received),
        "held_prices": sorted(held.values(), key=lambda line: line.item.name.lower()),
        "repeats": sum(1 for file in files if file.state == InvoiceBatchFile.State.IGNORED),
        "needs_checking": [inv for inv in invoices.values() if inv.status == Invoice.Status.CONFLICT],
        "failed": [file for file in files if file.state == InvoiceBatchFile.State.FAILED],
        "undo_until": undo_until,
        "can_undo": bool(undo_until and undo_until > timezone.now()),
    }


def undo_batch(batch, files):
    """Put back everything every invoice in this batch received.

    Each invoice keeps its own snapshot and window, but the manager imported one
    batch and undoes one batch, so this undoes them all while the batch's window
    is open. The window is the latest of the invoices' own, because a 50-file
    run reads the first file well before the last and the first one's ten
    minutes shouldn't decide it.

    Newest first, because the invoices in a batch can build on each other: a
    part delivery splits the rest of an order into a back-order line, and a
    later invoice in the same batch can receive against that line. Undoing the
    earlier one first would try to delete a back-order the later one still
    points at, which OrderLine's PROTECT refuses. Unwound in reverse, each
    invoice releases what the one before it left.

    ponytail: bounded by the batch's newest undo_until rather than each
    invoice's. If a run ever takes long enough for that gap to matter, give
    InvoiceBatch its own deadline set when the run finishes.
    """
    summary = batch_summary(batch, files)
    if not summary["can_undo"]:
        return 0
    undone = 0
    for invoice in (Invoice.objects.filter(pk__in=[file.invoice_id for file in files if file.invoice_id],
                                           undo_snapshot__isnull=False).order_by("-pk")):
        undo_ingest(invoice)
        undone += 1
    return undone


def save_invoice(invoice, lines):
    """Persist an invoice and its lines in one transaction, replacing the lines it had."""
    with transaction.atomic():
        if invoice.pk:
            invoice.lines.all().delete()
        invoice.save()
        for line in lines:
            line.pk, line.invoice = None, invoice
        InvoiceLine.objects.bulk_create(lines)
        link_documents(invoice)
    return invoice


def keep_document(organisation, user, filename, content_type, data, issued_on=None):
    """Keep the uploaded file itself, as the record behind the parsed lines.

    A GST-registered practice has to hold the original for seven years after
    the end of the income year (TAA 1994 s 22(2), GST Act 1985 s 75(3)), and
    the parsed lines are only StockRoom's reading of it. Called on the way in,
    before anything is confirmed, so a file is never read and then lost.

    The same bytes are kept once per practice: uploading a duplicate returns
    the copy already held rather than storing it twice.
    """
    checksum = hashlib.sha256(data).hexdigest()
    held = InvoiceDocument.objects.for_org(organisation).filter(checksum=checksum).first()
    if held is not None:
        return held
    # Dated from the invoice where it says so, otherwise today, which is the
    # later of the two and so never shortens how long it's kept for.
    document = InvoiceDocument(
        organisation=organisation,
        filename=(filename or "invoice")[:255],
        content_type=content_type or "application/octet-stream",
        data=data,
        byte_size=len(data),
        checksum=checksum,
        uploaded_by=user,
        retain_until=nztax.retain_until(issued_on or timezone.localdate()),
    )
    try:
        document.save()
    except IntegrityError:
        # Two uploads of the same file at once; the other one won.
        return InvoiceDocument.objects.for_org(organisation).filter(checksum=checksum).first()
    return document


def link_documents(invoice):
    """Point the kept file at the invoice it was read into, matched on the
    checksum of the same bytes. Also puts `retain_until` onto the invoice's own
    income year, now that the issue date is known."""
    if not invoice.checksum:
        return
    document = (InvoiceDocument.objects.for_org(invoice.organisation)
                .filter(checksum=invoice.checksum).first())
    if document is None:
        return
    fields = []
    if document.invoice_id is None:
        document.invoice = invoice
        fields.append("invoice")
    if invoice.issued_on:
        wanted = nztax.retain_until(invoice.issued_on)
        if wanted > document.retain_until:  # never shorten a retention period
            document.retain_until = wanted
            fields.append("retain_until")
    if fields:
        document.save(update_fields=fields)


def _text(value, max_length):
    return str(value or "").strip()[:max_length]


def _number(value):
    text = str(value if value is not None else "").replace("$", "").replace(",", "").strip()
    try:
        number = Decimal(text) if text else None
    except InvalidOperation:
        return None
    return number if number is None or number.is_finite() else None


def cents(value):
    return None if value is None else value.quantize(ONE_CENT)


def _date(value):
    text = _text(value, 40)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007 - a date, no time to zone
        except ValueError:
            pass
    return None
