import csv
import uuid
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from statistics import median
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required, ai_required
from accounts.mailto import build_mailto_link
from accounts.models import User

from . import invoices
from .chart import usage_chart_svg
from .csv_import import parse_csv
from .forecast import Status, forecast, outlier_mask, weekly_consumption
from .forms import ItemForm, SupplierForm
from .humanize import (
    CONFIDENCE_LABEL,
    arriving_text,
    build_caveat,
    build_sentence,
    format_qty,
    format_rate,
    humanize_range,
    humanize_run_out,
    order_by_text,
    phone_digits,
    pluralize_unit,
)
from .matching import UNDO_DAYS, Match, Matcher, normalize, record_merge, undo_merge
from .models import (
    CatalogueProduct,
    Invoice,
    InvoiceLine,
    Item,
    ItemAlias,
    OrderLine,
    StockEvent,
    Supplier,
)
from .spending import PERIOD_WEEKS, period_bounds, previous_period_bounds

CHART_WEEKS = 12

UNDO_WINDOW = timedelta(minutes=10)


def _org_items(org):
    return (
        Item.objects.for_org(org)
        .filter(is_active=True)
        .select_related("supplier", "reorder_requested_by")
        .prefetch_related("events", "order_lines", "other_suppliers")
    )


def _backups(item):
    """The item's other suppliers that are still active: the ones it could switch to."""
    return [s for s in item.other_suppliers.all() if s.is_active]


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


def _earliest_expected(item):
    open_orders = [o for o in item.order_lines.all() if o.is_open]
    if not open_orders:
        return None
    return min(o.expected_at for o in open_orders).date()


def _row(item, f, today):
    if f.status == Status.ON_ORDER:
        expected = _earliest_expected(item)
        days_label = arriving_text(expected, today) if expected else "On order"
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


@login_not_required
def home(request):
    """The practice's "what to order today" list, or for someone logged out,
    the public page saying what StockRoom is."""
    if not request.user.is_authenticated:
        context = {"demo_mode": settings.DEMO_MODE, "signup_enabled": settings.SIGNUP_ENABLED}
        return render(request, "stock/landing.html", context)
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
    now = timezone.localtime()
    today = now.date()
    events = list(item.events.all())
    open_orders = [o for o in item.order_lines.all() if o.is_open]
    f = forecast(events, open_orders, lead_days=item.supplier.lead_days, order_size=item.order_size, now=now)

    # weekly_consumption/outlier_mask are newest-first; the chart reads left
    # to right chronologically, so both get reversed together afterwards.
    weeks_newest_first = weekly_consumption(events, now, window_weeks=CHART_WEEKS)
    mask_newest_first = outlier_mask(weeks_newest_first)
    weeks = list(reversed(weeks_newest_first))
    mask = list(reversed(mask_newest_first))

    context = {
        "item": item,
        "f": f,
        "sentence": build_sentence(item, f, today),
        "confidence_label": CONFIDENCE_LABEL[f.confidence],
        "caveat": build_caveat(f),
        "chart_history_text": _chart_history_text(len(weeks), sum(mask)),
        "chart_svg": usage_chart_svg(weeks, mask),
        # On the list because it's running low, so it stays there until it's ordered
        # (a pin by hand is item.pinned_to_reorder_at). An item on order isn't on it.
        "wanted": f.status in WANTED_STATUSES,
        "backups": _backups(item),
        # Suppliers the manager could add as another source for this item.
        "addable_suppliers": Supplier.objects.for_org(request.user.organisation).filter(is_active=True)
        .exclude(pk=item.supplier_id).exclude(pk__in=item.other_suppliers.all()).order_by("name"),
        "price_label": f"${item.price:.2f} / {item.unit}" if item.price is not None else "Not set",
    }
    return render(request, "stock/item_detail.html", context)


def _chart_history_text(weeks_shown, excluded):
    base = f"{weeks_shown} week{'s' if weeks_shown != 1 else ''} of history shown"
    if excluded:
        base += f" · {excluded} week{'s' if excluded != 1 else ''} excluded from the average"
    return base


def _parse_nonneg_int(raw):
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _maybe_update_order_size(item, raw):
    raw = (raw or "").strip()
    if not raw:
        return
    try:
        size = int(raw)
    except ValueError:
        return
    if size >= 1:
        item.order_size = size
        item.save(update_fields=["order_size"])


def item_count_sheet(request, pk):
    """Setting the exact on-shelf count. Anyone logged in can correct a
    count (it's still just a StockEvent, attributed to whoever did it);
    only admins also get to change the standard order size here."""
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    f = _forecast_for(item, timezone.localtime())
    context = {
        "item": item,
        "current_qty": f.on_hand,
        "order_size_placeholder": item.order_size or f.order_qty,
        "error": None,
    }
    return render(request, "stock/item_count_sheet.html", context)


@require_POST
def item_count_save(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    qty = _parse_nonneg_int(request.POST.get("qty", "").strip())
    if qty is None:
        f = _forecast_for(item, timezone.localtime())
        context = {
            "item": item,
            "current_qty": f.on_hand,
            "order_size_placeholder": item.order_size or f.order_qty,
            "error": "Enter a whole number of 0 or more.",
        }
        return render(request, "stock/item_count_sheet.html", context)

    if request.user.is_org_admin:
        _maybe_update_order_size(item, request.POST.get("order_size"))
    return _log_event(
        request, item, "count", qty, f"Counted {item.name}: {format_qty(qty, item.unit)} · the forecast is updated"
    )


@require_POST
@admin_required
def item_set_price(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    raw = request.POST.get("price", "").strip()
    if raw:
        try:
            item.price = Decimal(raw)
        except InvalidOperation:
            messages.error(request, "Enter a valid price.")
            return redirect("stock:item_detail", pk=pk)
    else:
        item.price = None
    item.save(update_fields=["price"])
    messages.success(request, f"Price updated for {item.name}.")
    return redirect("stock:item_detail", pk=pk)


@require_POST
@admin_required
def item_set_order_size(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    raw = request.POST.get("order_size", "").strip()
    if raw:
        try:
            qty = int(raw)
            if qty < 1:
                raise ValueError
        except ValueError:
            messages.error(request, "Enter a whole number of 1 or more.")
            return redirect("stock:item_detail", pk=pk)
        item.order_size = qty
    else:
        item.order_size = None
    item.save(update_fields=["order_size"])
    messages.success(request, f"Order size updated for {item.name}.")
    return redirect("stock:item_detail", pk=pk)


def _id(raw):
    """A pk from a query or form value; anything else can't match, so it 404s."""
    return int(raw) if (raw or "").isdigit() else 0


def _back_to(request, default):
    """Where to go after a switch: the page it came from, if it's one of ours."""
    target = request.GET.get("next", "")
    return target if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}) else default


def _go(request, url):
    """Undo buttons post through htmx, so they need HX-Redirect; forms get a plain redirect."""
    if request.headers.get("HX-Request"):
        response = HttpResponse(status=200)
        response["HX-Redirect"] = url
        return response
    return redirect(url)


@require_POST
@admin_required
def item_switch_supplier(request, pk):
    """Order from one of the item's other suppliers from now on (?to=<supplier>).
    The old preferred supplier becomes a backup, so switching back is the same tap."""
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    new = get_object_or_404(item.other_suppliers.filter(is_active=True), pk=_id(request.GET.get("to")))
    old = item.supplier
    item.supplier = new
    item.save(update_fields=["supplier"])
    item.other_suppliers.remove(new)
    item.other_suppliers.add(old)
    back = _back_to(request, reverse("stock:reorder_list"))
    undo = f"{reverse('stock:item_switch_supplier', args=[item.pk])}?{urlencode({'to': old.pk, 'next': back})}"
    messages.success(request, f"{item.name} now comes from {new.name}. {old.name} is kept as a backup.", extra_tags=undo)
    return _go(request, back)


@require_POST
@admin_required
def item_other_suppliers(request, pk):
    """Add (add=<supplier>) or remove (remove=<supplier>) one of an item's backup suppliers."""
    org = request.user.organisation
    item = get_object_or_404(Item.objects.for_org(org), pk=pk)
    if request.POST.get("add"):
        supplier = get_object_or_404(Supplier.objects.for_org(org).filter(is_active=True), pk=_id(request.POST["add"]))
        if supplier != item.supplier:
            item.other_suppliers.add(supplier)
            messages.success(request, f"{supplier.name} added as another supplier for {item.name}.")
    elif request.POST.get("remove"):
        supplier = get_object_or_404(item.other_suppliers.all(), pk=_id(request.POST["remove"]))
        item.other_suppliers.remove(supplier)
        messages.success(request, f"{supplier.name} removed from {item.name}'s suppliers.")
    return redirect("stock:item_detail", pk=pk)


@require_POST
@admin_required
def item_toggle_reorder(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    item.pinned_to_reorder_at = None if item.pinned_to_reorder_at else timezone.now()
    item.save(update_fields=["pinned_to_reorder_at"])
    verb = "Added" if item.pinned_to_reorder_at else "Removed"
    messages.success(request, f"{verb} {item.name} {'to' if verb == 'Added' else 'from'} the reorder list.")
    return redirect("stock:item_detail", pk=pk)


def _coming_soon(request, title):
    return render(request, "stock/coming_soon.html", {"title": title})


def log_usage(request):
    """The capture grid. Search and the tap sheet run in the browser, so the
    service worker's cached copy of this page still works with no signal."""
    now = timezone.localtime()
    tiles = []
    for item in _org_items(request.user.organisation):
        f = _forecast_for(item, now)
        has_qty = f.on_hand is not None
        tiles.append(
            {
                "id": item.pk,
                "name": item.name,
                "unit": item.unit,
                "qty": format_qty(f.on_hand, item.unit),
                "status_color": STATUS_COLOR[f.status],
                # In words as well as the stripe's colour; OK needs neither.
                "status_label": "" if f.status == Status.OK else f.status.label,
                "weekly_usage": f.weekly_usage,
                "has_qty": has_qty,
                "after": format_qty(max(0, f.on_hand - 1), item.unit) if has_qty else "",
            }
        )
    tiles.sort(key=lambda t: t["weekly_usage"], reverse=True)
    return render(request, "stock/log_usage.html", {"tiles": tiles})


# How far a replayed offline tap's own timestamp is trusted.
MAX_REPLAY_AGE = timedelta(days=7)
# ponytail: phone clocks drift a little; anything further ahead than this is a bad clock, not skew.
CLOCK_SKEW = timedelta(minutes=5)


def _replay_time(raw, now):
    """The time a queued offline tap happened, or None if it can't be trusted."""
    when = parse_datetime(raw)
    if when is None or timezone.is_naive(when) or when > now + CLOCK_SKEW or when < now - MAX_REPLAY_AGE:
        return None
    return min(when, now)


def _log_event(request, item, kind, qty, message_text):
    """Every capture tap sends a client_id minted on the device before its
    first attempt, so a tap the server saved but whose reply got lost dedupes
    when the service worker replays it. Replays also carry occurred_at."""
    if request.POST.get("user_id", str(request.user.pk)) != str(request.user.pk):
        # Queued by someone else on a shared device: leave it for them to sync.
        return HttpResponse("Logged by a different user.", status=409)
    try:
        client_id = uuid.UUID(request.POST["client_id"]) if request.POST.get("client_id") else None
    except ValueError:
        return HttpResponse("Bad client_id.", status=400)

    now = timezone.now()
    replayed = "occurred_at" in request.POST
    created_at = _replay_time(request.POST["occurred_at"], now) if replayed else now
    if created_at is None:
        return HttpResponse("Tap time is in the future or more than 7 days old.", status=400)

    fields = {"organisation": item.organisation, "item": item, "user": request.user, "kind": kind, "qty": qty,
              "created_at": created_at}
    if client_id:
        event, created = StockEvent.objects.get_or_create(client_id=client_id, defaults=fields)
    else:
        event, created = StockEvent.objects.create(**fields), True

    if replayed:
        # The device shows its own "synced" confirmation; no undo for a tap from hours ago.
        return HttpResponse(status=200)
    if created:
        messages.success(request, message_text, extra_tags=reverse("stock:log_undo", args=[event.pk]))
    # Stay where the tap came from (the grid, an item, home), so the next item is
    # one tap away, like a tap queued offline. The reload shows the new count.
    response = HttpResponse(status=200)
    response["HX-Refresh"] = "true"
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
    return _toast_response(request, "Undone.")


SESSION_KEY = "stocktake"


@admin_required
def stocktake_step(request):
    org = request.user.organisation
    state = request.session.get(SESSION_KEY)
    if not state:
        item_ids = list(_org_items(org).order_by("name").values_list("pk", flat=True))
        if not item_ids:
            messages.info(request, "No items to count yet.")
            return redirect("stock:home")
        state = {"item_ids": item_ids, "index": 0}
        request.session[SESSION_KEY] = state

    if state["index"] >= len(state["item_ids"]):
        del request.session[SESSION_KEY]
        messages.success(request, "Stocktake complete. Every item has a fresh count.")
        return redirect("stock:home")

    item = get_object_or_404(Item.objects.for_org(org), pk=state["item_ids"][state["index"]])
    f = _forecast_for(item, timezone.localtime())
    context = {
        "item": item,
        "index": state["index"],
        "total": len(state["item_ids"]),
        "current_qty": f.on_hand,
        "order_size_placeholder": item.order_size or f.order_qty,
        "error": None,
    }
    return render(request, "stock/stocktake_step.html", context)


@require_POST
@admin_required
def stocktake_save(request):
    org = request.user.organisation
    state = request.session.get(SESSION_KEY)
    if not state or state["index"] >= len(state["item_ids"]):
        return redirect("stock:stocktake_step")

    item = get_object_or_404(Item.objects.for_org(org), pk=state["item_ids"][state["index"]])
    qty = _parse_nonneg_int(request.POST.get("qty", "").strip())
    if qty is None:
        f = _forecast_for(item, timezone.localtime())
        context = {
            "item": item,
            "index": state["index"],
            "total": len(state["item_ids"]),
            "current_qty": f.on_hand,
            "order_size_placeholder": item.order_size or f.order_qty,
            "error": "Enter a whole number of 0 or more.",
        }
        return render(request, "stock/stocktake_step.html", context)

    _maybe_update_order_size(item, request.POST.get("order_size"))
    StockEvent.objects.create(organisation=org, item=item, user=request.user, kind="count", qty=qty)

    state["index"] += 1
    request.session[SESSION_KEY] = state
    request.session.modified = True
    return redirect("stock:stocktake_step")


WANTED_STATUSES = {Status.OUT, Status.ORDER_NOW, Status.ORDER_THIS_WEEK}


def _split_items(org, now):
    """(item, forecast) pairs, split into those on the reorder list (urgent, or
    pinned there by hand) and the rest."""
    wanted, rest = [], []
    for item in _org_items(org):
        f = _forecast_for(item, now)
        (wanted if f.status in WANTED_STATUSES or item.pinned_to_reorder_at else rest).append((item, f))
    return wanted, rest


def _wanted_items(org, now):
    return _split_items(org, now)[0]


def _order_email(org, admin, supplier, lines, today):
    body_lines = [
        f"- {line['item'].name} (currently {format_qty(line['on_hand'], line['item'].unit)}, "
        f"order {line['suggested_qty']} {pluralize_unit(line['item'].unit, line['suggested_qty'])}, "
        f"{order_by_text(line['order_by'], today) or 'when you can'})"
        for line in lines
    ]
    body = (
        "Kia ora,\n\nCould you please send through:\n\n"
        + "\n".join(body_lines)
        + f"\n\nThanks,\n{admin.name or admin.email}\n{org.name}"
    )
    return build_mailto_link(supplier.email, f"Stock order for {supplier.name}", body)


def _reorder_groups(org, admin, wanted, today):
    by_supplier = {}
    for item, f in wanted:
        by_supplier.setdefault(item.supplier, []).append((item, f))

    groups = []
    for supplier, entries in sorted(by_supplier.items(), key=lambda kv: kv[0].name):
        lines = [
            {
                "item": item,
                "on_hand": f.on_hand,
                "sub": f"{format_qty(f.on_hand, item.unit)} · {format_rate(f.weekly_usage, item.unit)}",
                "status_label": f.status.label,
                "status_color": STATUS_COLOR[f.status],
                "solid": f.status in SOLID_STATUSES,
                "order_by": f.order_by,
                "order_by_text": order_by_text(f.order_by, today),
                "suggested_qty": item.order_size or f.order_qty,
                "backups": _backups(item),
            }
            for item, f in sorted(entries, key=lambda e: e[0].name)
        ]
        groups.append(
            {
                "supplier": supplier,
                "phone_digits": phone_digits(supplier.phone),
                "mailto": _order_email(org, admin, supplier, lines, today) if supplier.email else "",
                "lines": lines,
            }
        )
    return groups


def reorder_list(request):
    org = request.user.organisation
    now = timezone.localtime()
    wanted, rest = _split_items(org, now)
    rest.sort(key=lambda e: e[0].name)
    context = {
        "wanted_count": len(wanted),
        # Asked for by an assistant, waiting for a manager to add or turn down.
        "asked_for": [
            {"item": item, "sub": f"{format_qty(f.on_hand, item.unit)} · {format_rate(f.weekly_usage, item.unit)}"}
            for item, f in rest
            if item.reorder_requested_by
        ],
        "addable": [item for item, f in rest if not item.reorder_requested_by],
    }

    if request.user.is_org_admin:
        context["groups"] = _reorder_groups(org, request.user, wanted, now.date())
        return render(request, "stock/reorder_list.html", context)

    by_supplier = {}
    for item, f in wanted:
        by_supplier.setdefault(item.supplier.name, []).append(item.name)
    context["by_supplier"] = sorted(by_supplier.items())
    return render(request, "stock/reorder_list_assistant.html", context)


@require_POST
def reorder_add(request, pk):
    """A manager's tap puts the item straight on the list. An assistant's asks
    a manager to, from the top of the manager's reorder list."""
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    undo_url = reverse("stock:reorder_add_undo", args=[item.pk])
    if request.user.is_org_admin:
        item.pinned_to_reorder_at = timezone.now()
        item.save(update_fields=["pinned_to_reorder_at"])
        messages.success(request, f"Added {item.name} to the reorder list.", extra_tags=undo_url)
    else:
        item.reorder_requested_by = request.user
        item.save(update_fields=["reorder_requested_by"])
        messages.success(request, f"Asked the manager to add {item.name}.", extra_tags=undo_url)
    return redirect("stock:reorder_list")


@require_POST
def reorder_add_undo(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    if request.user.is_org_admin:
        # A request the manager just said yes to goes back to waiting.
        item.pinned_to_reorder_at = None
    elif item.reorder_requested_by_id == request.user.pk:
        item.reorder_requested_by = None
    else:
        raise PermissionDenied
    item.save(update_fields=["pinned_to_reorder_at", "reorder_requested_by"])
    messages.success(request, "Undone.")
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:reorder_list")
    return response


@require_POST
@admin_required
def reorder_decline(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    item.reorder_requested_by = None
    item.save(update_fields=["reorder_requested_by"])
    messages.success(request, f"{item.name} is off the asked-for list. You can still add it below.")
    return redirect("stock:reorder_list")


def _default_qty(item, f):
    return item.order_size or f.order_qty


def _off_the_list(item):
    """Ordered: any pin or assistant's request has been dealt with."""
    item.pinned_to_reorder_at = None
    item.reorder_requested_by = None
    item.save(update_fields=["pinned_to_reorder_at", "reorder_requested_by"])


@require_POST
@admin_required
def reorder_mark_ordered(request, pk):
    org = request.user.organisation
    item = get_object_or_404(Item.objects.for_org(org), pk=pk)
    f = _forecast_for(item, timezone.localtime())
    qty = _parse_nonneg_int(request.POST.get(f"qty_{item.pk}", "").strip()) or _default_qty(item, f)

    order = OrderLine.objects.create(
        organisation=org, item=item, qty=qty, unit_price=item.price, ordered_by=request.user
    )
    _off_the_list(item)

    messages.success(
        request,
        f"Ordered {format_qty(qty, item.unit)} of {item.name} from {item.supplier.name}.",
        extra_tags=reverse("stock:reorder_undo", args=[order.pk]),
    )
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:reorder_list")
    return response


@require_POST
@admin_required
def reorder_mark_supplier_ordered(request, pk):
    org = request.user.organisation
    supplier = get_object_or_404(Supplier.objects.for_org(org), pk=pk)
    now = timezone.localtime()
    items = [(item, f) for item, f in _wanted_items(org, now) if item.supplier_id == supplier.pk]
    if not items:
        return redirect("stock:reorder_list")

    order_ids = []
    for item, f in items:
        qty = _parse_nonneg_int(request.POST.get(f"qty_{item.pk}", "").strip()) or _default_qty(item, f)
        order = OrderLine.objects.create(
            organisation=org, item=item, qty=qty, unit_price=item.price, ordered_by=request.user
        )
        order_ids.append(order.pk)
        _off_the_list(item)

    undo_url = reverse("stock:reorder_undo_batch") + "?ids=" + ",".join(str(i) for i in order_ids)
    messages.success(
        request, f"Ordered {len(items)} item{'s' if len(items) != 1 else ''} from {supplier.name}.", extra_tags=undo_url
    )
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:reorder_list")
    return response


def _cancel_if_undoable(order):
    if order.cancelled_at is None and order.received_at is None and timezone.now() - order.ordered_at <= UNDO_WINDOW:
        order.cancelled_at = timezone.now()
        order.save(update_fields=["cancelled_at"])
        return True
    return False


@require_POST
@admin_required
def reorder_undo(request, pk):
    order = get_object_or_404(OrderLine.objects.for_org(request.user.organisation), pk=pk)
    if not _cancel_if_undoable(order):
        raise PermissionDenied
    return _toast_response(request, "Undone.")


@require_POST
@admin_required
def reorder_undo_batch(request):
    ids = [i for i in request.GET.get("ids", "").split(",") if i.isdigit()]
    orders = OrderLine.objects.for_org(request.user.organisation).filter(pk__in=ids)
    for order in orders:
        _cancel_if_undoable(order)
    return _toast_response(request, "Undone.")


def _open_lines_for_org(org):
    return (
        OrderLine.objects.for_org(org)
        .filter(received_at__isnull=True, cancelled_at__isnull=True)
        .select_related("item", "supplier")
        .order_by("expected_at")
    )


def deliveries(request):
    org = request.user.organisation
    now = timezone.localtime()
    today = now.date()

    by_supplier = {}
    for line in _open_lines_for_org(org):
        by_supplier.setdefault(line.supplier, []).append(line)

    groups = []
    for supplier, lines in sorted(by_supplier.items(), key=lambda kv: kv[0].name):
        groups.append(
            {
                "supplier": supplier,
                "lines": [
                    {
                        "order": line,
                        "expected_text": arriving_text(line.expected_at.date(), today),
                        "is_late": line.expected_at < now,
                    }
                    for line in lines
                ],
            }
        )

    context = {
        "groups": groups,
        "open_count": sum(len(g["lines"]) for g in groups),
        "can_edit_price": request.user.is_org_admin,
    }
    return render(request, "stock/deliveries.html", context)


@require_POST
def delivery_submit(request, pk):
    """One form per supplier: either one line's "Receive" button or the
    supplier's "Receive all" button was pressed - both roles can use it."""
    org = request.user.organisation
    supplier = get_object_or_404(Supplier.objects.for_org(org), pk=pk)
    open_lines = list(_open_lines_for_org(org).filter(supplier=supplier))

    if "receive_line" in request.POST:
        target_ids = {request.POST["receive_line"]}
    elif "receive_all" in request.POST:
        target_ids = {str(line.pk) for line in open_lines}
    else:
        return redirect("stock:deliveries")

    received = 0
    for line in open_lines:
        if str(line.pk) not in target_ids:
            continue
        qty = _parse_nonneg_int(request.POST.get(f"qty_{line.pk}", "").strip())
        if not qty or qty > line.qty:
            continue

        unit_price = None
        if request.user.is_org_admin:
            raw_price = request.POST.get(f"price_{line.pk}", "").strip()
            if raw_price:
                try:
                    unit_price = Decimal(raw_price)
                except InvalidOperation:
                    pass

        line.receive(qty, request.user, unit_price)
        received += 1

    if received:
        messages.success(request, f"Received {received} item{'s' if received != 1 else ''} from {supplier.name}.")
    else:
        messages.error(request, "Nothing was received. Check the quantities.")
    return redirect("stock:deliveries")


def _items_context(org, form=None, query=""):
    item_list = Item.objects.for_org(org).filter(is_active=True).select_related("supplier").order_by("name")
    if query:
        item_list = item_list.filter(name__icontains=query)
    return {
        "item_list": item_list,
        "form": form or ItemForm(instance=Item(organisation=org)),
        "query": query,
        "total_count": Item.objects.for_org(org).filter(is_active=True).count(),
    }


@admin_required
def items(request):
    org = request.user.organisation
    query = request.GET.get("q", "").strip()
    context = _items_context(org, query=query)
    template = "stock/items.html#item_rows" if request.headers.get("HX-Request") else "stock/items.html"
    return render(request, template, context)


@require_POST
@admin_required
def item_add(request):
    org = request.user.organisation
    form = ItemForm(request.POST, instance=Item(organisation=org))
    if form.is_valid():
        starting_count = form.cleaned_data.get("starting_count")
        item = form.save()
        if starting_count is not None:
            StockEvent.objects.create(organisation=org, item=item, user=request.user, kind="count", qty=starting_count)
        messages.success(request, f"Added {item.name}.")
        return redirect("stock:items")
    return render(request, "stock/items.html", _items_context(org, form=form))


@admin_required
def item_view_row(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation).select_related("supplier"), pk=pk)
    return render(request, "stock/_item_row.html", {"item": item})


@admin_required
def item_edit_row(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    form = ItemForm(instance=item)
    return render(request, "stock/_item_row.html", {"item": item, "edit_form": form})


@require_POST
@admin_required
def item_update_row(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    form = ItemForm(request.POST, instance=item)
    if form.is_valid():
        item = form.save()
        item.other_suppliers.remove(item.supplier)  # now the preferred one, so no longer a backup
        item = Item.objects.select_related("supplier").get(pk=item.pk)
        return render(request, "stock/_item_row.html", {"item": item})
    return render(request, "stock/_item_row.html", {"item": item, "edit_form": form})


@require_POST
@admin_required
def item_archive(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    item.is_active = False
    item.save(update_fields=["is_active"])
    messages.success(request, f"Archived {item.name}.", extra_tags=reverse("stock:item_unarchive", args=[item.pk]))
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:items")
    return response


@require_POST
@admin_required
def item_unarchive(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    item.is_active = True
    item.save(update_fields=["is_active"])
    return _toast_response(request, "Restored.")


@admin_required
def item_import(request):
    return render(request, "stock/item_import.html")


@require_POST
@admin_required
def item_import_preview(request):
    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, "Choose a CSV file first.")
        return redirect("stock:item_import")

    try:
        text = csv_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "That file doesn't look like a CSV.")
        return redirect("stock:item_import")
    return import_preview(request, text)


def import_preview(request, text):
    """Check CSV text and show what would be imported. Nothing is saved until
    item_import_confirm; also used by the AI import (assistant.views)."""
    org = request.user.organisation
    active_supplier_names = {
        n.lower() for n in Supplier.objects.for_org(org).filter(is_active=True).values_list("name", flat=True)
    }
    rows, file_errors = parse_csv(text, active_supplier_names)
    if file_errors:
        messages.error(request, " ".join(file_errors))
        return redirect("stock:item_import")

    valid_rows = [r for r in rows if r.is_valid]
    # A row that's already on the stock list isn't a new item: sure matches are
    # skipped, and anything less is shown so the manager can say it's the same.
    suppliers = {s.name.lower(): s for s in Supplier.objects.for_org(org).filter(is_active=True)}
    matches = Matcher(org).match_all(
        [{"name": r.name, "supplier": suppliers.get(r.supplier_name.lower()), "price": r.price} for r in valid_rows]
    )
    for index, (row, match) in enumerate(zip(valid_rows, matches, strict=True)):
        row.index, row.match = index, match
    request.session["pending_import"] = [
        {
            "name": r.name,
            "unit": r.unit,
            "supplier_name": r.supplier_name,
            "price": str(r.price) if r.price is not None else None,
            "order_size": r.order_size,
            "count": r.count,
            "match": [r.match.item.pk, r.match.confidence, r.match.method] if r.match.item else None,
        }
        for r in valid_rows
    ]
    sure_count = sum(r.match.band == "sure" for r in valid_rows)
    context = {
        "rows": rows,
        "valid_count": len(valid_rows) - sure_count,
        "sure_count": sure_count,
        "unsure_count": sum(r.match.band in ("likely", "check") for r in valid_rows),
        "error_count": len(rows) - len(valid_rows),
    }
    return render(request, "stock/item_import_preview.html", context)


@require_POST
@admin_required
def item_import_confirm(request):
    org = request.user.organisation
    pending = request.session.pop("pending_import", None)
    if not pending:
        messages.error(request, "Nothing to import. Upload a CSV first.")
        return redirect("stock:item_import")

    created = merged = 0
    for index, row in enumerate(pending):
        if row.get("match"):
            pk, confidence, method = row["match"]
            match = Match(Item.objects.for_org(org).filter(pk=pk, is_active=True).first(), confidence, method)
            if match.item and (match.band == "sure" or request.POST.get(f"same_{index}")):
                record_merge(match, name=row["name"], user=request.user, source=ItemAlias.Source.IMPORT)
                merged += 1
                continue
        supplier = Supplier.objects.for_org(org).filter(name__iexact=row["supplier_name"]).first()
        if not supplier:
            continue
        item = Item.objects.create(
            organisation=org,
            name=row["name"],
            unit=row["unit"],
            supplier=supplier,
            price=Decimal(row["price"]) if row["price"] else None,
            order_size=row["order_size"],
        )
        if row["count"] is not None:
            StockEvent.objects.create(organisation=org, item=item, user=request.user, kind="count", qty=row["count"])
        created += 1

    already = f" {merged} {'was' if merged == 1 else 'were'} already on your list." if merged else ""
    messages.success(request, f"Imported {created} item{'s' if created != 1 else ''}.{already}")
    return redirect("stock:items")


@ai_required
@admin_required
def invoice_upload(request):
    return render(request, "stock/invoice_upload.html")


INVOICE_FIELDS = ("supplier_name", "order_ref", "invoice_number", "checksum", "source_name")
# Invoices that changed nothing, so they can be checked and imported again.
RECHECKABLE = [Invoice.Status.IGNORED, Invoice.Status.CONFLICT]
# Why a manager imports a repeat anyway: picked, not typed.
FORCE_REASONS = [
    "It's a different invoice with the same number",
    "The same order arrived twice",
    "Something else",
]


def invoice_preview(request, invoice, lines):
    """Show a read invoice with its lines to check, and hold it in the session
    until invoice_confirm. Used for a new upload (assistant.views) and for
    checking a saved one again (invoice_check)."""
    invoices.match_lines(invoice, lines)
    for index, line in enumerate(lines):
        line.index, line.adds_up = index, invoices.adds_up(line)
    request.session["pending_invoice"] = {
        "invoice_id": invoice.pk,
        "supplier_id": invoice.supplier_id,
        "issued_on": invoice.issued_on.isoformat() if invoice.issued_on else None,
        **{name: getattr(invoice, name) for name in INVOICE_FIELDS},
        "lines": [
            {
                "sku": line.sku,
                "description": line.description,
                "qty": line.qty,
                "unit_price": _text_or_none(line.unit_price),
                "line_total": _text_or_none(line.line_total),
                "match": [line.match.item.pk, line.match.confidence, line.match.method] if line.match.item else None,
            }
            for line in lines
        ],
    }
    repeat = invoice.status == Invoice.Status.IGNORED
    return render(request, "stock/invoice_preview.html", {
        "invoice": invoice,
        "lines": lines,
        "suppliers": Supplier.objects.for_org(invoice.organisation).filter(is_active=True).order_by("name"),
        "force_reasons": FORCE_REASONS if repeat else None,
        "original": invoices.find_original(invoice) if repeat else None,
    })


def _text_or_none(value):
    return None if value is None else str(value)


def _decimal_or_none(text):
    return None if text is None else Decimal(text)


def _edited_qty(raw, fallback):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback


def _edited_price(raw, fallback):
    try:
        value = Decimal(raw)
    except (TypeError, ValueError, InvalidOperation):
        return fallback
    return value if value >= 0 else fallback


@require_POST
@admin_required
def invoice_confirm(request):
    org = request.user.organisation
    pending = request.session.pop("pending_invoice", None)
    if not pending:
        messages.error(request, "Nothing to import. Upload an invoice first.")
        return redirect("stock:invoice_upload")

    invoice, force_reason = Invoice(organisation=org), ""
    if pending.get("invoice_id"):
        invoice = Invoice.objects.for_org(org).filter(pk=pending["invoice_id"], status__in=RECHECKABLE).first()
        if invoice is None:
            messages.error(request, "That invoice has already been imported.")
            return redirect("stock:invoice_detail", pending["invoice_id"])
        if invoice.status == Invoice.Status.IGNORED:
            force_reason = request.POST.get("force_reason", "")
            if force_reason not in FORCE_REASONS:
                messages.error(request, "Choose why you're importing it again.")
                return redirect("stock:invoice_check", invoice.pk)
    for name in INVOICE_FIELDS:
        setattr(invoice, name, pending.get(name, ""))
    invoice.issued_on = date.fromisoformat(pending["issued_on"]) if pending["issued_on"] else None
    # A supplier picked in the preview, for an invoice from one StockRoom didn't recognise.
    supplier_id = _id(request.POST.get("supplier")) or pending["supplier_id"]
    invoice.supplier = Supplier.objects.for_org(org).filter(pk=supplier_id).first() if supplier_id else None

    lines = []
    for index, row in enumerate(pending["lines"]):
        qty = _edited_qty(request.POST.get(f"qty_{index}"), row["qty"])
        unit_price = invoices.cents(_edited_price(request.POST.get(f"price_{index}"), _decimal_or_none(row["unit_price"])))
        line_total = invoices.cents(_edited_price(request.POST.get(f"total_{index}"),
                                                  _decimal_or_none(row.get("line_total"))))
        line = InvoiceLine(organisation=org, sku=row["sku"], description=row["description"],
                           qty=qty, unit_price=unit_price, line_total=line_total)
        if row["match"]:
            pk, confidence, method = row["match"]
            match = Match(Item.objects.for_org(org).filter(pk=pk, is_active=True).first(), confidence, method)
            if match.item and (match.band == "sure" or request.POST.get(f"same_{index}")):
                line.item, line.match = match.item, match
        lines.append(line)

    invoice = invoices.ingest(invoice, lines, request.user, force_reason)
    if invoice.status == Invoice.Status.CONFLICT:
        messages.error(request, "Saved, but it needs checking before it counts.")
    elif invoice.status != Invoice.Status.IGNORED:  # the invoice page says it was a repeat
        messages.success(request, "Invoice added.")
    return redirect("stock:invoice_detail", invoice.pk)


@admin_required
def invoice_check(request, pk):
    """A repeat to import anyway, or an invoice that needs checking, back in the preview."""
    invoice = get_object_or_404(Invoice.objects.for_org(request.user.organisation), pk=pk, status__in=RECHECKABLE)
    return invoice_preview(request, invoice, list(invoice.lines.order_by("pk")))


@admin_required
def invoice_detail(request, pk):
    invoice = get_object_or_404(Invoice.objects.for_org(request.user.organisation), pk=pk)
    return render(request, "stock/invoice_detail.html", {
        "invoice": invoice,
        "lines": invoice.lines.select_related("item").order_by("pk"),
        "original": invoices.find_original(invoice) if invoice.status == Invoice.Status.IGNORED else None,
    })


def _stocked_names(org):
    return {normalize(n) for n in Item.objects.for_org(org).filter(is_active=True).values_list("name", flat=True)}


def _catalogue_product(pk):
    return get_object_or_404(CatalogueProduct.objects.prefetch_related("offers__supplier"), pk=pk)


def _supplier_choices(org, product):
    """[(catalogue supplier name, the practice's own active Supplier of that name or None)], in catalogue order."""
    ours = {s.name.lower(): s for s in Supplier.objects.for_org(org).filter(is_active=True)}
    return [(offer.supplier.name, ours.get(offer.supplier.name.lower())) for offer in product.offers.all()]


@admin_required
def catalogue(request):
    """Stock NZ practices commonly order, and who sells it, less what this practice already has."""
    stocked = _stocked_names(request.user.organisation)
    products = [
        p for p in CatalogueProduct.objects.prefetch_related("offers__supplier").order_by("position")
        if normalize(p.name) not in stocked
    ]
    return render(request, "stock/catalogue.html", {"products": products})


@admin_required
def catalogue_sheet(request, pk):
    """Who to order a catalogue product from, in the bottom sheet. A supplier
    the practice already uses is picked to start with."""
    product = _catalogue_product(pk)
    choices = _supplier_choices(request.user.organisation, product)
    default = next((name for name, ours in choices if ours), choices[0][0])
    return render(request, "stock/_catalogue_sheet.html", {"product": product, "choices": choices, "default": default})


@require_POST
@admin_required
def catalogue_add(request, pk):
    """Copy a catalogue product into the practice's stock, ordered from the
    supplier they picked. Others they already use who sell it become backups."""
    org = request.user.organisation
    product = _catalogue_product(pk)
    choices = dict(_supplier_choices(org, product))
    chosen = request.POST.get("supplier", "")
    if chosen not in choices:
        messages.error(request, "Choose who you'll order it from.")
        return redirect("stock:catalogue")
    if normalize(product.name) in _stocked_names(org):
        messages.info(request, f"{product.name} is already on your stock list.")
        return redirect("stock:catalogue")

    preferred, note = choices[chosen], ""
    if preferred is None:
        # An archived supplier of that name comes back rather than clashing on the name.
        preferred = Supplier.objects.for_org(org).filter(name__iexact=chosen).first()
        if preferred:
            preferred.is_active = True
            preferred.save(update_fields=["is_active"])
        else:
            preferred = Supplier.objects.create(organisation=org, name=chosen)
        note = f" {preferred.name} is now one of your suppliers: set how long their deliveries take on the Suppliers page."
    item = Item.objects.create(organisation=org, name=product.name, unit=product.unit, supplier=preferred)
    item.other_suppliers.set([s for s in choices.values() if s and s != preferred])
    messages.success(request, f"Added {item.name}.{note}", extra_tags=reverse("stock:catalogue_undo", args=[item.pk]))
    return redirect("stock:catalogue")


@require_POST
@admin_required
def catalogue_undo(request, pk):
    item = get_object_or_404(Item.objects.for_org(request.user.organisation), pk=pk)
    if item.events.exists() or item.order_lines.exists():
        raise PermissionDenied  # it's been counted or ordered since; archive it instead
    item.delete()
    messages.success(request, "Undone.")
    return _go(request, reverse("stock:catalogue"))


@admin_required
def merges(request):
    """Names from invoices and imports matched to an existing item lately, each with Undo."""
    since = timezone.now() - timedelta(days=UNDO_DAYS)
    aliases = (
        ItemAlias.objects.for_org(request.user.organisation)
        .filter(created_at__gte=since)
        .select_related("item", "source_invoice", "created_by")
        .order_by("-created_at")
    )
    return render(request, "stock/merges.html", {"aliases": aliases, "undo_days": UNDO_DAYS})


@require_POST
@admin_required
def merge_undo(request, pk):
    alias = get_object_or_404(ItemAlias.objects.for_org(request.user.organisation).select_related("item"), pk=pk)
    if not undo_merge(alias, request.user):
        raise PermissionDenied  # already undone, or older than UNDO_DAYS
    return _toast_response(request, f"Undone. {alias.raw_name} won't be matched to {alias.item.name} again.")


def _median_lead_days(supplier):
    """Actual ordered -> received time over the last 10 deliveries, or None
    with fewer than 1. A suggestion only - it never changes lead_days itself."""
    lines = OrderLine.objects.filter(supplier=supplier, received_at__isnull=False).order_by("-received_at")[:10]
    diffs = [(line.received_at - line.ordered_at).days for line in lines]
    return round(median(diffs)) if diffs else None


def _suppliers_context(org, form=None):
    supplier_list = (
        Supplier.objects.for_org(org)
        .filter(is_active=True)
        .annotate(item_count=Count("items", filter=Q(items__is_active=True)))
        .order_by("name")
    )
    for supplier in supplier_list:
        supplier.phone_digits = phone_digits(supplier.phone)
        supplier.actual_lead_days = _median_lead_days(supplier)
    return {"suppliers": supplier_list, "form": form or SupplierForm(instance=Supplier(organisation=org))}


def _annotate_item_count(supplier):
    supplier.item_count = supplier.items.filter(is_active=True).count()
    supplier.phone_digits = phone_digits(supplier.phone)
    supplier.actual_lead_days = _median_lead_days(supplier)
    return supplier


@admin_required
def suppliers(request):
    return render(request, "stock/suppliers.html", _suppliers_context(request.user.organisation))


@require_POST
@admin_required
def supplier_add(request):
    org = request.user.organisation
    form = SupplierForm(request.POST, instance=Supplier(organisation=org))
    if form.is_valid():
        form.save()
        messages.success(request, f"Added {form.instance.name}.")
        return redirect("stock:suppliers")
    return render(request, "stock/suppliers.html", _suppliers_context(org, form=form))


@admin_required
def supplier_edit(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    form = SupplierForm(instance=supplier)
    return render(request, "stock/_supplier_row.html", {"supplier": supplier, "edit_form": form})


@admin_required
def supplier_row(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    return render(request, "stock/_supplier_row.html", {"supplier": _annotate_item_count(supplier)})


@require_POST
@admin_required
def supplier_update(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    form = SupplierForm(request.POST, instance=supplier)
    if form.is_valid():
        form.save()
        return render(request, "stock/_supplier_row.html", {"supplier": _annotate_item_count(supplier)})
    return render(request, "stock/_supplier_row.html", {"supplier": supplier, "edit_form": form})


@require_POST
@admin_required
def supplier_lead_days(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    delta = 1 if request.POST.get("direction") == "up" else -1
    supplier.lead_days = max(1, min(60, supplier.lead_days + delta))
    supplier.save(update_fields=["lead_days"])
    return render(request, "stock/_supplier_row.html", {"supplier": _annotate_item_count(supplier)})


@require_POST
@admin_required
def supplier_apply_lead_days(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    actual = _median_lead_days(supplier)
    if actual:
        supplier.lead_days = max(1, min(60, actual))
        supplier.save(update_fields=["lead_days"])
    return render(request, "stock/_supplier_row.html", {"supplier": _annotate_item_count(supplier)})


@require_POST
@admin_required
def supplier_archive(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    active_items = supplier.items.filter(is_active=True).count()
    if active_items:
        messages.error(
            request, f"{supplier.name} still has {active_items} active item(s) - move or archive them first."
        )
    else:
        supplier.is_active = False
        supplier.save(update_fields=["is_active"])
        messages.success(
            request, f"Archived {supplier.name}.", extra_tags=reverse("stock:supplier_unarchive", args=[supplier.pk])
        )
    response = HttpResponse(status=200)
    response["HX-Redirect"] = reverse("stock:suppliers")
    return response


@require_POST
@admin_required
def supplier_unarchive(request, pk):
    supplier = get_object_or_404(Supplier.objects.for_org(request.user.organisation), pk=pk)
    supplier.is_active = True
    supplier.save(update_fields=["is_active"])
    return _toast_response(request, "Restored.")


def _spend_total(org, start, end):
    lines = OrderLine.objects.for_org(org).filter(
        ordered_at__date__gte=start, ordered_at__date__lt=end, cancelled_at__isnull=True, unit_price__isnull=False
    )
    return sum((line.qty * line.unit_price for line in lines), Decimal(0))


@admin_required
def spending(request):
    org = request.user.organisation
    period = request.GET.get("period", "week")
    if period not in PERIOD_WEEKS:
        period = "week"
    today = timezone.localtime().date()
    start, end = period_bounds(period, today)

    lines = list(
        OrderLine.objects.for_org(org)
        .filter(ordered_at__date__gte=start, ordered_at__date__lt=end, cancelled_at__isnull=True, unit_price__isnull=False)
        .select_related("item", "supplier")
    )
    actual_total = sum((line.qty * line.unit_price for line in lines), Decimal(0))

    supplier_totals, item_totals = {}, {}
    for line in lines:
        amount = line.qty * line.unit_price
        supplier_totals[line.supplier.name] = supplier_totals.get(line.supplier.name, Decimal(0)) + amount
        item_totals[line.item.name] = item_totals.get(line.item.name, Decimal(0)) + amount

    by_supplier = sorted(
        (
            {"name": name, "total": total, "pct": round(total / actual_total * 100) if actual_total else 0}
            for name, total in supplier_totals.items()
        ),
        key=lambda s: -s["total"],
    )
    top_items = sorted(
        ({"name": name, "total": total} for name, total in item_totals.items()), key=lambda i: -i["total"]
    )[:5]

    # A meaningful comparison needs 3 full periods of history behind it, or
    # a new practice's mostly-empty early weeks would just look like savings.
    prev_bounds = previous_period_bounds(period, start, 3)
    earliest = OrderLine.objects.for_org(org).order_by("ordered_at").first()
    has_comparison = bool(earliest) and earliest.ordered_at.date() <= prev_bounds[0][0]
    average_prev = trend = None
    if has_comparison:
        prev_totals = [_spend_total(org, p_start, p_end) for p_start, p_end in prev_bounds]
        average_prev = round(sum(prev_totals) / 3, 2)
        trend = "up" if actual_total > average_prev else "down" if actual_total < average_prev else "same"

    now = timezone.localtime()
    estimated_total = 0.0
    missing_price_count = 0
    for item in _org_items(org):
        if item.price is None:
            missing_price_count += 1
            continue
        f = _forecast_for(item, now)
        estimated_total += f.weekly_usage * float(item.price) * PERIOD_WEEKS[period]

    context = {
        "period": period,
        "period_label": {"week": "this week", "month": "this month", "year": "this year"}[period],
        "actual_total": actual_total,
        "by_supplier": by_supplier,
        "top_items": top_items,
        "average_prev": average_prev,
        "trend": trend,
        "estimated_total": round(estimated_total, 2),
        "missing_price_count": missing_price_count,
    }
    return render(request, "stock/spending.html", context)


def _toast_response(request, message):
    """An undo from a toast: the page behind it is out of date, so reload it and say so."""
    messages.success(request, message)
    response = HttpResponse(status=200)
    response["HX-Refresh"] = "true"
    return response


def _activity_entries(org, item_id, user_id):
    events = StockEvent.objects.for_org(org).select_related("item", "user")
    orders = OrderLine.objects.for_org(org).select_related("item", "supplier", "ordered_by", "received_by")

    if item_id:
        events = events.filter(item_id=item_id)
        orders = orders.filter(item_id=item_id)

    # Filtered by user at the entry level, not the queryset: an order line
    # ordered by one person and received by another is two separate entries,
    # each attributed to just the person who did that part.
    entries = []
    for event in events:
        entries.append({"when": event.created_at, "who_id": event.user_id, "who": event.user.name or event.user.email, "what": str(event)})
    for order in orders:
        entries.append(
            {
                "when": order.ordered_at,
                "who_id": order.ordered_by_id,
                "who": order.ordered_by.name or order.ordered_by.email,
                "what": f"Ordered {format_qty(order.qty, order.item.unit)} of {order.item.name} from {order.supplier.name}",
            }
        )
        if order.received_at:
            entries.append(
                {
                    "when": order.received_at,
                    "who_id": order.received_by_id,
                    "who": (order.received_by.name or order.received_by.email) if order.received_by else "someone",
                    "what": f"Received {format_qty(order.received_qty, order.item.unit)} of {order.item.name}",
                }
            )

    if user_id:
        entries = [e for e in entries if str(e["who_id"]) == str(user_id)]

    entries.sort(key=lambda e: e["when"], reverse=True)
    return entries


@admin_required
def activity_log(request):
    org = request.user.organisation
    item_id = request.GET.get("item", "").strip()
    user_id = request.GET.get("user", "").strip()

    entries = _activity_entries(org, item_id if item_id.isdigit() else "", user_id if user_id.isdigit() else "")
    page = Paginator(entries, 25).get_page(request.GET.get("page"))

    context = {
        "page": page,
        "items": Item.objects.for_org(org).order_by("name"),
        "users": User.objects.for_org(org).order_by("name"),
        "item_id": item_id,
        "user_id": user_id,
    }
    return render(request, "stock/activity_log.html", context)


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM, so Excel opens UTF-8 text correctly
    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)
    return response


@admin_required
def export_items(request):
    org = request.user.organisation
    rows = [
        [item.name, item.unit, item.supplier.name, item.price, item.order_size, item.is_active]
        for item in Item.objects.for_org(org).select_related("supplier").order_by("name")
    ]
    return _csv_response("items.csv", ["name", "unit", "supplier", "price", "order_size", "active"], rows)


@admin_required
def export_stock_events(request):
    org = request.user.organisation
    rows = [
        [event.created_at.isoformat(), event.item.name, event.get_kind_display(), event.qty, event.user.name or event.user.email]
        for event in StockEvent.objects.for_org(org).select_related("item", "user").order_by("created_at")
    ]
    return _csv_response("stock_events.csv", ["date", "item", "kind", "qty", "user"], rows)


@admin_required
def export_order_lines(request):
    org = request.user.organisation
    rows = [
        [
            order.ordered_at.isoformat(),
            order.item.name,
            order.supplier.name,
            order.qty,
            order.unit_price,
            order.ordered_by.name or order.ordered_by.email,
            order.expected_at.isoformat(),
            order.received_qty,
            order.received_at.isoformat() if order.received_at else "",
            (order.received_by.name or order.received_by.email) if order.received_by else "",
            order.cancelled_at.isoformat() if order.cancelled_at else "",
        ]
        for order in OrderLine.objects.for_org(org)
        .select_related("item", "supplier", "ordered_by", "received_by")
        .order_by("ordered_at")
    ]
    return _csv_response(
        "order_lines.csv",
        [
            "ordered_at", "item", "supplier", "qty", "unit_price", "ordered_by",
            "expected_at", "received_qty", "received_at", "received_by", "cancelled_at",
        ],
        rows,
    )
