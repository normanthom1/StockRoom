"""What Ask StockRoom knows about the practice: plain-text lines for the
Gemini prompt, built fresh for each question.

Prices and spending go in for managers only. For an assistant they're never
built at all, so the model can't leak what it was never given, the same
rule as every page an assistant can open.
"""

from decimal import Decimal

from django.utils import timezone

from stock.humanize import format_qty, format_rate
from stock.models import Invoice, OrderLine
from stock.spending import period_bounds, previous_period_bounds
from stock.views import _split_items


def practice_snapshot(user, now):
    org = user.organisation
    today = now.date()
    wanted, rest = _split_items(org, now)
    on_list = {item.pk for item, _ in wanted}
    spend = _spend_by_item(org, today) if user.is_org_admin else {}

    lines = [f"Practice: {org.name}. Today is {today:%A} {today.day} {today:%B %Y}.", "", "Items:"]
    for item, f in sorted(wanted + rest, key=lambda e: e[0].name.lower()):
        parts = [
            item.name,
            f"preferred supplier {item.supplier.name}, about {item.supplier.lead_days} days to arrive",
            f"on hand {format_qty(f.on_hand, item.unit)}",
            f"uses {format_rate(f.weekly_usage, item.unit)}",
            f"status {f.status.label}",
        ]
        backups = [s.name for s in item.other_suppliers.all() if s.is_active]
        if backups:
            parts.append(f"also available from {', '.join(backups)}")
        if f.days_left is not None:
            parts.append(f"about {round(f.days_left)} days left")
        if f.incoming:
            parts.append(f"{format_qty(f.incoming, item.unit)} on order")
        parts.append("on the reorder list" if item.pk in on_list else "not on the reorder list")
        if item.reorder_requested_by:
            parts.append(f"{item.reorder_requested_by.name} asked for it to go on the list")
        if user.is_org_admin:
            parts.append(f"${item.price} per {item.unit}" if item.price is not None else "no price set")
            month, year = spend.get(item.pk, (Decimal(0), Decimal(0)))
            parts.append(f"spent ${month:.2f} this month, ${year:.2f} this year")
        lines.append("- " + "; ".join(parts))

    if user.is_org_admin:
        lines += ["", "Spending (orders placed, at the price when ordered):"]
        for label, (start, end) in _periods(today).items():
            total = sum((line.qty * line.unit_price for line in _priced_orders(org, start, end)), Decimal(0))
            lines.append(f"- {label} ({start:%d %b} to {end:%d %b}): ${total:.2f}")
        lines += _back_order_lines(org)
        lines += _invoice_lines(org)
    return "\n".join(lines)


def _back_order_lines(org):
    open_lines = (
        OrderLine.objects.for_org(org)
        .filter(received_at__isnull=True, cancelled_at__isnull=True)
        .select_related("item", "supplier")
        .order_by("expected_at")
    )
    if not open_lines:
        return []
    lines = ["", "Still on order or back-order:"]
    for line in open_lines:
        expected = timezone.localtime(line.expected_at).date()
        bit = f"- {line.qty} {line.item.name} from {line.supplier.name}, ordered {line.ordered_at.date():%d %b}, expected {expected:%d %b}"
        if line.split_from_id:
            bit += " (back-order, the rest of an earlier delivery)"
        lines.append(bit)
    return lines


def _invoice_lines(org):
    invoices = (
        Invoice.objects.for_org(org)
        .exclude(status=Invoice.Status.IGNORED)
        .prefetch_related("lines__item")
        .order_by("-issued_on", "-created_at")[:30]
    )
    if not invoices:
        return []
    lines = ["", "Invoices, most recent first (numbers here are exact, from the invoice):"]
    for invoice in invoices:
        date = f"{invoice.issued_on:%d %b %Y}" if invoice.issued_on else "date unknown"
        supplier = invoice.supplier.name if invoice.supplier_id else (invoice.supplier_name or "unknown supplier")
        bit = f"- {date}: {supplier}, {invoice.get_status_display().lower()}"
        if invoice.invoice_number:
            bit += f", invoice number {invoice.invoice_number}"
        items = [
            f"{line.qty if line.qty is not None else '?'} {line.item.name if line.item_id else (line.description or line.sku or 'unmatched line')}"
            + (f" at ${line.unit_price} each" if line.unit_price is not None else "")
            for line in invoice.lines.all()
        ]
        if items:
            bit += " - " + "; ".join(items)
        lines.append(bit)
    return lines


def _periods(today):
    week, month, year = (period_bounds(p, today) for p in ("week", "month", "year"))
    [last_month] = previous_period_bounds("month", month[0], 1)
    return {"This week": week, "This month": month, "Last month": last_month, "This year": year}


def _priced_orders(org, start, end):
    return OrderLine.objects.for_org(org).filter(
        ordered_at__date__gte=start, ordered_at__date__lt=end, cancelled_at__isnull=True, unit_price__isnull=False
    )


def _spend_by_item(org, today):
    """{item_id: (this month's spend, this year's spend)}."""
    month_start, _ = period_bounds("month", today)
    year_start, year_end = period_bounds("year", today)
    spend = {}
    for line in _priced_orders(org, year_start, year_end):
        month, year = spend.get(line.item_id, (Decimal(0), Decimal(0)))
        amount = line.qty * line.unit_price
        in_month = timezone.localtime(line.ordered_at).date() >= month_start
        spend[line.item_id] = (month + amount if in_month else month, year + amount)
    return spend
