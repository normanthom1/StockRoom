import json
from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
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
from .models import Item, StockEvent

UNDO_WINDOW = timedelta(minutes=10)


def _org_items(org):
    return (
        Item.objects.for_org(org)
        .filter(is_active=True)
        .select_related("supplier")
        .prefetch_related("events", "order_lines")
    )


def _forecast_for(item, now):
    open_orders = [o for o in item.order_lines.all() if o.is_open]
    return forecast(item.events.all(), open_orders, lead_days=item.supplier.lead_days, order_size=item.order_size, now=now)

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

    rows, fine_rows = [], []
    for item in _org_items(org):
        f = _forecast_for(item, now)
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
    org = request.user.organisation
    now = timezone.localtime()

    tiles = []
    for item in _org_items(org):
        f = _forecast_for(item, now)
        tiles.append(
            {
                "id": item.pk,
                "name": item.name,
                "qty": format_qty(f.on_hand, item.unit),
                "status_color": STATUS_COLOR[f.status],
                "weekly_usage": f.weekly_usage,
            }
        )
    tiles.sort(key=lambda t: t["weekly_usage"], reverse=True)

    query = request.GET.get("q", "").strip()
    if query:
        tiles = [t for t in tiles if query.lower() in t["name"].lower()]

    context = {"tiles": tiles, "total_count": Item.objects.for_org(org).filter(is_active=True).count(), "query": query}
    template = "stock/log_usage.html#tiles" if request.headers.get("HX-Request") else "stock/log_usage.html"
    return render(request, template, context)


def log_usage_sheet(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    f = _forecast_for(item, timezone.localtime())
    has_qty = f.on_hand is not None
    context = {
        "item": item,
        "has_qty": has_qty,
        "current": format_qty(f.on_hand, item.unit) if has_qty else "",
        "after": format_qty(max(0, f.on_hand - 1), item.unit) if has_qty else "",
    }
    return render(request, "stock/log_usage_sheet.html", context)


def _log_event(request, item, kind, qty, message_text):
    event = StockEvent.objects.create(organisation=item.organisation, item=item, user=request.user, kind=kind, qty=qty)
    messages.success(request, message_text, extra_tags=reverse("stock:log_undo", args=[event.pk]))
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:home")
    return response


@require_POST
def log_used_one(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    return _log_event(request, item, "used", 1, f"Logged: used 1 {item.unit} of {item.name} · the manager will see it")


@require_POST
def log_running_low(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    return _log_event(request, item, "low", None, f"Logged: running low on {item.name} · the manager will see it")


@require_POST
def log_used_last(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    return _log_event(request, item, "out", None, f"Logged: used the last {item.name} · the manager will see it")


@require_POST
def log_undo(request, pk):
    event = get_object_or_404(StockEvent.objects.for_org(request.user.organisation), pk=pk)
    if event.user_id != request.user.id or timezone.now() - event.created_at > UNDO_WINDOW:
        raise PermissionDenied
    event.delete()
    return _toast_response("Undone.")


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
