import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from statistics import median

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import admin_required
from accounts.mailto import build_mailto_link

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
from .models import Item, OrderLine, StockEvent, Supplier

CHART_WEEKS = 12

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
        "on_reorder_list": bool(item.pinned_to_reorder_at) or f.status != Status.OK,
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


@admin_required
def item_count_sheet(request, pk):
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
@admin_required
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
        messages.success(request, "Stocktake complete - every item has a fresh count.")
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


def _wanted_items(org, now):
    """(item, forecast) pairs that belong on the reorder list: urgent, or pinned there by hand."""
    wanted = []
    for item in _org_items(org):
        f = _forecast_for(item, now)
        if f.status in WANTED_STATUSES or item.pinned_to_reorder_at:
            wanted.append((item, f))
    return wanted


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


def _reorder_groups(org, admin, now):
    today = now.date()
    by_supplier = {}
    for item, f in _wanted_items(org, now):
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

    if request.user.is_org_admin:
        groups = _reorder_groups(org, request.user, now)
        context = {"groups": groups, "wanted_count": sum(len(g["lines"]) for g in groups)}
        return render(request, "stock/reorder_list.html", context)

    wanted = _wanted_items(org, now)
    by_supplier = {}
    for item, f in wanted:
        by_supplier.setdefault(item.supplier.name, []).append(item.name)
    context = {"by_supplier": sorted(by_supplier.items()), "wanted_count": len(wanted)}
    return render(request, "stock/reorder_list_assistant.html", context)


def _default_qty(item, f):
    return item.order_size or f.order_qty


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
    item.pinned_to_reorder_at = None
    item.save(update_fields=["pinned_to_reorder_at"])

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
        item.pinned_to_reorder_at = None
        item.save(update_fields=["pinned_to_reorder_at"])

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
    return _toast_response("Undone.")


@require_POST
@admin_required
def reorder_undo_batch(request):
    ids = [i for i in request.GET.get("ids", "").split(",") if i.isdigit()]
    orders = OrderLine.objects.for_org(request.user.organisation).filter(pk__in=ids)
    for order in orders:
        _cancel_if_undoable(order)
    return _toast_response("Undone.")


def _open_lines_for_org(org):
    return (
        OrderLine.objects.for_org(org)
        .filter(received_at__isnull=True, cancelled_at__isnull=True)
        .select_related("item", "item__supplier")
        .order_by("expected_at")
    )


def deliveries(request):
    org = request.user.organisation
    now = timezone.localtime()
    today = now.date()

    by_supplier = {}
    for line in _open_lines_for_org(org):
        by_supplier.setdefault(line.item.supplier, []).append(line)

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


def _receive_line(order, received_qty, user, unit_price=None):
    """Receive part or all of an open order line. Any unreceived remainder
    splits off into a new open line, so it stays tracked as back-ordered."""
    remainder = order.qty - received_qty
    order.received_qty = received_qty
    order.received_at = timezone.now()
    order.received_by = user
    if unit_price is not None:
        order.unit_price = unit_price
        order.item.price = unit_price
        order.item.save(update_fields=["price"])
    order.save()
    StockEvent.objects.create(
        organisation=order.organisation, item=order.item, user=user, kind="received", qty=received_qty
    )
    if remainder > 0:
        OrderLine.objects.create(
            organisation=order.organisation,
            item=order.item,
            qty=remainder,
            unit_price=order.unit_price,
            ordered_by=order.ordered_by,
            ordered_at=order.ordered_at,
            expected_at=order.expected_at,
        )


@require_POST
def delivery_submit(request, pk):
    """One form per supplier: either one line's "Receive" button or the
    supplier's "Receive all" button was pressed - both roles can use it."""
    org = request.user.organisation
    supplier = get_object_or_404(Supplier.objects.for_org(org), pk=pk)
    open_lines = list(_open_lines_for_org(org).filter(item__supplier=supplier))

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

        _receive_line(line, qty, request.user, unit_price)
        received += 1

    if received:
        messages.success(request, f"Received {received} item{'s' if received != 1 else ''} from {supplier.name}.")
    else:
        messages.error(request, "Nothing was received - check the quantities.")
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
        form.save()
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
    return _toast_response("Restored.")


@admin_required
def item_import(request):
    return render(request, "stock/item_import.html")


@require_POST
@admin_required
def item_import_preview(request):
    org = request.user.organisation
    csv_file = request.FILES.get("csv_file")
    if not csv_file:
        messages.error(request, "Choose a CSV file first.")
        return redirect("stock:item_import")

    try:
        text = csv_file.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "That file doesn't look like a CSV.")
        return redirect("stock:item_import")

    active_supplier_names = {
        n.lower() for n in Supplier.objects.for_org(org).filter(is_active=True).values_list("name", flat=True)
    }
    rows, file_errors = parse_csv(text, active_supplier_names)
    if file_errors:
        messages.error(request, " ".join(file_errors))
        return redirect("stock:item_import")

    valid_rows = [r for r in rows if r.is_valid]
    request.session["pending_import"] = [
        {
            "name": r.name,
            "unit": r.unit,
            "supplier_name": r.supplier_name,
            "price": str(r.price) if r.price is not None else None,
            "order_size": r.order_size,
            "count": r.count,
        }
        for r in valid_rows
    ]
    context = {"rows": rows, "valid_count": len(valid_rows), "error_count": len(rows) - len(valid_rows)}
    return render(request, "stock/item_import_preview.html", context)


@require_POST
@admin_required
def item_import_confirm(request):
    org = request.user.organisation
    pending = request.session.pop("pending_import", None)
    if not pending:
        messages.error(request, "Nothing to import - upload a CSV first.")
        return redirect("stock:item_import")

    created = 0
    for row in pending:
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

    messages.success(request, f"Imported {created} item{'s' if created != 1 else ''}.")
    return redirect("stock:items")


def _median_lead_days(supplier):
    """Actual ordered -> received time over the last 10 deliveries, or None
    with fewer than 1. A suggestion only - it never changes lead_days itself."""
    lines = OrderLine.objects.filter(item__supplier=supplier, received_at__isnull=False).order_by("-received_at")[:10]
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
    return _toast_response("Restored.")


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
