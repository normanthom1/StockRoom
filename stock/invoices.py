"""Reading a supplier invoice (PDF, photo or CSV) into Invoice and InvoiceLine rows.

Photos and PDFs go through Gemini; a CSV is read directly. Both come out in
the same shape, keyed by MAPPING, so the checks below run once. Only the
parsed lines are saved, never the file.
"""

import csv
import io
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.db import transaction

from assistant import gemini

from .models import Invoice, InvoiceLine, Supplier

# Invoice field name -> ours. A CSV can use either name as its header.
MAPPING = {
    "invoice_date": "date",
    "vendor": "supplier",
    "po_number": "order_id",
    "sku": "sku",
    "description": "description",
    "qty": "qty",
    "unit": "unit_price",
    "line_total": "line_total",
}
HEADER_FIELDS = ("date", "supplier", "order_id")
CSV_TYPES = ("text/csv", "application/vnd.ms-excel")  # Excel on Windows labels CSVs as the latter
ONE_CENT = Decimal("0.01")
DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y")

# ponytail: a keyword match on the code, or the description when there's no code.
# So a codeless "Total Etch" is dropped and freight with a numeric code is kept;
# add a "kind" to the AI schema if either turns up.
NOT_A_PRODUCT = re.compile(r"\s*(sub[- ]?total|total|gst|freight|shipping|courier|discount|rounding)\b", re.IGNORECASE)

INVOICE_RULES = """You read a supplier invoice for a New Zealand dental practice into its details and product lines.
- invoice_date: the date it was issued, as YYYY-MM-DD. New Zealand writes dates day first.
- vendor: the supplier that sent it. If it's one of this practice's suppliers, use that name exactly as written here: {suppliers}.
- po_number: the practice's order or purchase order number, if shown.
- lines: one per product, in order. Skip freight, GST, discounts, subtotals and totals.
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
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
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


def parse_invoice(organisation, data, mime_type):
    """Read an invoice file into a saved Invoice with its product lines. It lands
    as parsed when every line adds up, otherwise as a conflict. Neither the
    invoice nor its lines are saved yet; save_invoice does that once a preview
    is confirmed. Raises gemini.GeminiError if a photo or PDF can't be read,
    and ValueError for any other kind of file."""
    if mime_type in CSV_TYPES:
        header, lines = _read_csv(data)
    elif mime_type == "application/pdf" or mime_type.startswith("image/"):
        header, lines = _read_with_ai(organisation, data, mime_type)
    else:
        raise ValueError(f"Can't read an invoice from {mime_type}.")
    return _build(organisation, header, lines)


def _read_with_ai(organisation, data, mime_type):
    suppliers = Supplier.objects.for_org(organisation).filter(is_active=True).order_by("name")
    rules = INVOICE_RULES.format(suppliers=", ".join(s.name for s in suppliers) or "(none set up yet)")
    reply = gemini.generate(rules, [("user", "Read the attached invoice.")], schema=INVOICE_SCHEMA,
                            attachment=(mime_type, data))
    if not isinstance(reply, dict):
        reply = {}
    header = {MAPPING[key]: reply.get(key) for key in ("invoice_date", "vendor", "po_number")}
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
        if not (sku or description) or NOT_A_PRODUCT.match(sku or description):
            continue
        qty, unit_price, line_total = _number(raw.get("qty")), _number(raw.get("unit_price")), _number(raw.get("line_total"))
        if qty is not None:
            qty = int(qty) if qty == qty.to_integral_value() and qty >= 0 else None
        if line_total is None and qty is not None and unit_price is not None:
            line_total = qty * unit_price
        lines.append(InvoiceLine(organisation=organisation, sku=sku, description=description, qty=qty,
                                 unit_price=unit_price, line_total=line_total))

    supplier_name = _text(header.get("supplier"), 200)
    invoice = Invoice(
        organisation=organisation,
        supplier_name=supplier_name,
        supplier=Supplier.objects.for_org(organisation).filter(name__iexact=supplier_name).first() if supplier_name else None,
        issued_on=_date(header.get("date")),
        order_ref=_text(header.get("order_id"), 64),
    )
    set_totals_ok(invoice, lines)
    for line in lines:  # checked above at full precision; stored to the cent
        line.unit_price = cents(line.unit_price)
        line.line_total = cents(line.line_total)
    return invoice, lines


def set_totals_ok(invoice, lines):
    """Every line's total must be within 1c of qty x unit price. Sets
    Invoice.totals_ok and status; also used to re-check after an admin edits
    a line's qty or unit price in the preview."""
    invoice.totals_ok = bool(lines) and all(
        None not in (line.qty, line.unit_price, line.line_total)
        and abs(line.line_total - line.qty * line.unit_price) <= ONE_CENT
        for line in lines
    )
    invoice.status = Invoice.Status.PARSED if invoice.totals_ok else Invoice.Status.CONFLICT


def save_invoice(invoice, lines):
    """Persist a built invoice and its lines in one transaction."""
    with transaction.atomic():
        invoice.save()
        for line in lines:
            line.invoice = invoice
        InvoiceLine.objects.bulk_create(lines)
    return invoice


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
