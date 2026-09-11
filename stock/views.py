import json

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required

from .forecast import Status, forecast
from .humanize import (
    format_qty,
    format_rate,
    humanize_range,
    humanize_run_out,
    order_by_text,
)
from .models import Item

# ui-design's 4 status colours; "on order" isn't one of them, so it borrows OK's.
STATUS_COLOR = {
    Status.OUT: "out",
    Status.ORDER_NOW: "now",
    Status.ORDER_THIS_WEEK: "week",
    Status.ON_ORDER: "ok",
    Status.OK: "ok",
}
SOLID_STATUSES = {Status.OUT, Status.ORDER_NOW}


def _row(item, f, today):
    if f.status == Status.ON_ORDER:
        days_label = "On order"
    elif f.status == Status.OUT:
        days_label = "Out now"
    elif f.confidence == "low" and f.run_out_range:
        lo, hi = ((d - today).days for d in f.run_out_range)
        days_label = humanize_range(lo, hi)
    elif f.days_left is not None:
        days_label = f"~{round(f.days_left)} days"
    else:
        days_label = ""

    return {
        "id": item.pk,
        "name": item.name,
        "sub": f"{format_qty(f.on_hand, item.unit)} · {format_rate(f.weekly_usage, item.unit)} · "
        f"{item.supplier.name} ~{item.supplier.lead_days} days",
        "status_label": f.status.label,
        "status_color": STATUS_COLOR[f.status],
        "solid": f.status in SOLID_STATUSES,
        "days_label": days_label,
        "order_by": "" if f.status == Status.ON_ORDER else order_by_text(f.order_by, today),
        "sort_key": f.days_left if f.days_left is not None else 9999,
        "fine_label": humanize_run_out(f.days_left),
    }


def home(request):
    org = request.user.organisation
    now = timezone.localtime()
    today = now.date()
    org_items = (
        Item.objects.for_org(org).filter(is_active=True).select_related("supplier").prefetch_related(
            "events", "order_lines"
        )
    )

    rows, fine_rows = [], []
    for item in org_items:
        open_orders = [o for o in item.order_lines.all() if o.is_open]
        f = forecast(item.events.all(), open_orders, lead_days=item.supplier.lead_days, order_size=item.order_size, now=now)
        row = _row(item, f, today)
        (fine_rows if f.status == Status.OK else rows).append(row)

    rows.sort(key=lambda r: r["sort_key"])
    fine_rows.sort(key=lambda r: r["name"])

    context = {"rows": rows, "fine_rows": fine_rows, "reorder_count": len(rows)}
    template = "stock/home.html#rows" if request.headers.get("HX-Request") else "stock/home.html"
    return render(request, template, context)


def item_detail(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    return render(request, "stock/coming_soon.html", {"title": item.name})


def _coming_soon(request, title):
    return render(request, "stock/coming_soon.html", {"title": title})


def log_usage(request):
    return _coming_soon(request, "Log usage")


def reorder_list(request):
    return _coming_soon(request, "Reorder list")


def deliveries(request):
    return _coming_soon(request, "Deliveries")


@admin_required
def items(request):
    return _coming_soon(request, "Stock")


@admin_required
def suppliers(request):
    return _coming_soon(request, "Suppliers")


@admin_required
def spending(request):
    return _coming_soon(request, "Spending")


def demo_sheet(request):
    return render(request, "stock/demo_sheet.html")


def _toast_response(message, undo_url=None):
    response = HttpResponse(status=204)
    response["HX-Trigger"] = json.dumps({"toast": {"message": message, "undo_url": undo_url}})
    return response


@require_POST
def demo_toast(request):
    return _toast_response("Logged: running low on gloves", reverse("stock:demo_undo"))


@require_POST
def demo_undo(request):
    return _toast_response("Undone.")
