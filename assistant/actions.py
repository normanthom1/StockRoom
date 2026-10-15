"""What Ask StockRoom can change: one entry in ACTIONS each, and every change
the chat makes goes through here.

The AI proposes and the server decides. Gemini is sent the declarations of
only the actions the person's role allows (what it's never given, it can't
call) and calls them instead of answering, once for each change in the message.
build() turns those calls into one card showing every change, or a plain
answer when none can be made, and nothing is written until the person taps
Confirm. A name that fits several items becomes a "Which one?" on the card, and
choosing re-runs build() with that pick and no AI call. apply() then checks the
role again and makes every line through the same code as the buttons on the
pages, returning the toast's text and one undo URL for all of them.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.http import QueryDict
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from stock import invoices
from stock.forecast import forecast
from stock.forms import ItemForm, SupplierForm
from stock.humanize import format_qty, pluralize_unit
from stock.matching import Matcher, _sizes, normalize, similarity, synonym_key
from stock.models import CatalogueProduct, Invoice, Item, StockEvent, Supplier
from stock.spending import PERIOD_WEEKS
from stock.views import (
    SOLID_STATUSES,
    STATUS_COLOR,
    WANTED_STATUSES,
    _backups,
    _default_qty,
    _forecast_for,
    _off_the_list,
    _open_lines_for_org,
    _reorder_groups,
    _stocked_names,
    _supplier_choices,
    _wanted_items,
    add_backup_supplier,
    add_from_catalogue,
    add_to_reorder_list,
    archive_blocked_by,
    fill_invoice,
    log_event,
    pending_invoice,
    place_order,
    receive_delivery_line,
    set_item_active,
    set_item_price,
    set_order_size,
    set_supplier_active,
    switch_supplier,
)

ANYONE = frozenset(User.Role.values)
ADMIN = frozenset({User.Role.ADMIN})
MONEY_REFUSAL = "Sorry, your account needs manager access to answer questions about prices or spending."
CANT = "Sorry, I can't do that from the chat."
MANAGER_JOB = "That's a job for the manager. The manager can do it from the Reorder list, or ask me."
# An undo that isn't a page's own: puts back the old values each line of the card recorded.
RESTORE = "restore"
MAX_QTY = 9999  # anything bigger is a misheard number, not a day's use
MIN_UNUSUAL = 200  # a count has to pass this, and 10x anything seen before, to be asked about
MAX_OPTIONS = 6  # buttons on a "Which one?"
SIMILAR = 0.85  # how alike a new name is to one already there before it's asked about
Kind = StockEvent.Kind


@dataclass(frozen=True)
class Action:
    name: str
    roles: frozenset  # the User.Role values that may use it
    declaration: dict | Callable  # what Gemini is told about it: description and parameters (or user -> that)
    preview: Callable  # (user, args, pending) -> a card line, a Which, or a plain answer when it can't be done
    apply: Callable | None  # (user, line, client_id) -> the id of what it made
    undo: str = "stock:log_undo_batch"  # URL name, given ?ids= of what apply made, or RESTORE; also the family of a card
    # A link action changes nothing, so it has no card or Confirm: preview returns buttons, or a plain answer.
    link: bool = False
    refusal: str = CANT  # what someone whose role lacks it is told if they get as far as asking
    restore: Callable | None = None  # (user, line) -> None; with undo=RESTORE, puts back what apply changed


@dataclass(frozen=True)
class Which:
    """A name that fits several things (items, suppliers, orders): asked, never guessed."""

    name: str
    options: list  # [{"id": pk, "name": ...}]
    kind: str = "item"  # what the ids are, so a pick can't be taken for another kind's
    ask: str = ""  # the question, when it isn't "Which one?" (a yes or no about something that already exists)


class Answer(str):
    """A plain answer that comes with buttons under it: [{"label", "url", "download"}]."""

    def __new__(cls, text, buttons=()):
        answer = super().__new__(cls, text)
        answer.buttons = list(buttons)
        return answer


def declarations(user):
    """The function declarations this person's role allows, for gemini.generate(tools=...)."""
    return [
        {"name": action.name, **(action.declaration(user) if callable(action.declaration) else action.declaration)}
        for action in ACTIONS.values() if user.role in action.roles
    ]


def propose(user, calls, text=""):
    """The card for the changes Gemini asked for, or a plain answer (with any
    buttons) when none can be made. text is whatever it wrote beside the calls."""
    # An assistant's message never carries a price on, even into the session.
    calls = [[name, {k: v for k, v in (args if isinstance(args, dict) else {}).items()
                     if user.is_org_admin or k != "price"}] for name, args in calls]
    return build(user, calls, text=text)


def build(user, calls, picks=None, text=""):
    """The card (a dict kept in the session) for calls, a list of [name, args].
    picks is {"<call index>.<kind>": id} for each "Which one?" already answered.
    A call that can't be made is a note on the card while another can; when
    none can, the first reason is the answer, as plain text."""
    picks = picks or {}
    lines, which, family = [], None, None
    pending = {}  # item pk -> the unsaved events earlier lines would make, so later ones start from them
    problems, buttons = [], []
    # A whole order last, so a line named on its own wins over the same line in it.
    for i in sorted(range(len(calls)), key=lambda n: calls[n][0] in ("receive_order", "mark_supplier_ordered")):
        name, args = calls[i]
        action = ACTIONS.get(name)
        if action is None or user.role not in action.roles:
            problems.append(action.refusal if action else CANT)
            continue
        # Only a pick on the card sets "picks", never the model.
        args = {key: value for key, value in args.items() if key != "picks"}
        args["picks"] = {key.split(".", 1)[1]: value for key, value in picks.items() if key.startswith(f"{i}.")}
        if action.link:
            result = action.preview(user, args, pending)
            if isinstance(result, str):
                problems.append(result)
                buttons += getattr(result, "buttons", [])
            else:
                buttons += result
            continue
        if family not in (None, action.undo):
            problems.append("That's a different kind of change, so ask again for it.")
            continue
        result = action.preview(user, args, pending)
        if isinstance(result, str):
            problems.append(result)
            buttons += getattr(result, "buttons", [])
            continue
        family = action.undo
        if isinstance(result, Which):
            which = which or {"key": f"{i}.{result.kind}", "name": result.name, "options": result.options,
                              "ask": result.ask}
        else:
            lines += [{"action": name, **line} for line in (result if isinstance(result, list) else [result])]
    if not lines and not which and not buttons and not problems:
        return CANT
    if not lines and not which:
        buttons = list({b["url"]: b for b in buttons}.values())
        if buttons:
            # Why something couldn't be done beats the model's own words, which may say it was.
            return Answer(" ".join(problems) or text or "Here you go.", buttons)
        return problems[0]
    summaries = [line["summary"] for line in lines] or [f"asked which '{which['name']}' you meant"]
    # A manager's order: what each line costs ("" when its item has no price), and the total over a few lines.
    costs = [Decimal(line["cost"]) for line in lines if line.get("cost")]
    return {
        "calls": calls, "picks": picks, "lines": lines, "which": which, "note": " ".join(problems),
        "summary": ", ".join(summaries[:-1]) + (" and " if len(summaries) > 1 else "") + summaries[-1],
        "total": f"{sum(costs):.2f}" if costs and len(lines) > 1 else "",
        "unpriced": sum(1 for line in lines if line.get("cost") == "") if costs and len(lines) > 1 else 0,
    }


def apply(user, proposal):
    """Make a confirmed card's changes in one transaction. None if this person
    may no longer make them (their role changed) or an item has gone. The undo
    URL is "" when there's nothing to undo (a receipt that received nothing)."""
    if "receipt" in proposal:
        return _apply_receipt_card(user, proposal)
    lines = proposal["lines"]
    todo = [(ACTIONS.get(line["action"]), line) for line in lines]
    if proposal.get("which") or not lines or any(a is None or user.role not in a.roles for a, _ in todo):
        return None
    card = uuid.UUID(proposal["id"])
    try:
        with transaction.atomic():
            # The card's id as each event's client_id: a second Confirm racing the first can't log it twice.
            ids = [action.apply(user, line, uuid.uuid5(card, str(n))) for n, (action, line) in enumerate(todo)]
    except ObjectDoesNotExist:
        return None
    for line, made in zip(lines, ids, strict=True):
        line["made"] = made  # what a restore needs to find a new item or supplier again
    undo = (RESTORE if todo[0][0].undo == RESTORE
            else reverse(todo[0][0].undo) + "?ids=" + ",".join(str(i) for i in ids))
    text = lines[0]["toast"] if len(lines) == 1 else lines[0]["many"].format(n=len(lines))
    return text, undo


def restore(user, change):
    """The Undo for a card whose actions have undo=RESTORE: each line puts back the
    old values it recorded. Only the roles that could make the change put it back."""
    with transaction.atomic():
        for line in change["lines"]:
            action = ACTIONS.get(line["action"])
            if action and action.restore and user.role in action.roles:
                action.restore(user, line)


def _item_for(user, args):
    """The item named: an Item, a Which when several fit, or a plain answer saying why not.
    Only a sure match is used as it is; anything less is put to the person."""
    org = user.organisation
    if "item" in args["picks"]:
        item = Item.objects.for_org(org).filter(pk=args["picks"]["item"], is_active=True).first()
        return item or "That item isn't in your stock any more."
    name = str(args.get("item") or "").strip()
    if not name:
        return "Which item was it?"
    matcher = Matcher(org)
    match = matcher.match(name)
    if match.band == "sure":
        return match.item
    # Not surely one item: every item with all the words they said ("gloves" fits gloves S, M and L).
    words = set(synonym_key(name).split())
    fits = [item for item, _, key in matcher.items if words <= set(key.split())]
    fits = fits or match.tied or ([match.item] if match.band == "likely" else [])
    if not fits:
        return f"I can't find '{name}' in your stock."
    if len(fits) > MAX_OPTIONS:
        return f"There are {len(fits)} items that fit '{name}'. Say a bit more of its name."
    return Which(name, [{"id": item.pk, "name": item.name} for item in sorted(fits, key=lambda i: i.name)])


def _similar(name, rows):
    """The rows (items or suppliers) already called about that: the same words, or spelt closely."""
    key = synonym_key(name)
    words = set(key.split())
    return [row for row in rows
            if (other := synonym_key(row.name)) == key or similarity(key, other) >= SIMILAR
            or (words and words <= set(other.split()) and len(words) > 1)]


def _chip(f):
    return {"status_label": f.status.label, "status_color": STATUS_COLOR[f.status], "solid": f.status in SOLID_STATUSES}


def _whole(value, lowest):
    """A count from Gemini (JSON numbers can arrive as 2.0), or None if it isn't a sensible one."""
    if isinstance(value, int | float) and lowest <= value <= MAX_QTY and value == int(value):
        return int(value)
    return None


def _unusual(item, qty):
    """More than this item has ever had: a mis-said number, not a shelf."""
    seen = [e.qty for e in item.events.all() if e.kind in (Kind.COUNT, Kind.RECEIVED) and e.qty]
    return qty > max(MIN_UNUSUAL, 10 * max([*seen, item.order_size or 0]))


def _flag(item, before, after):
    """What a change does to the reorder list, in words, or nothing."""
    pinned = bool(item.pinned_to_reorder_at)
    was = before.status in WANTED_STATUSES or pinned
    now = after.status in WANTED_STATUSES or pinned
    if now and not was:
        return "This puts it on the reorder list."
    if was and not now:
        return "This takes it off the reorder list."
    if before.status != after.status:
        return f"Its status changes to {after.status.label}."
    return ""


# --- Used N, used the last one, running low, and an exact count: Log usage's buttons and the item page's count ---

def _event_line(user, args, pending, kind):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    qty = None
    if kind in (Kind.USED, Kind.COUNT):
        qty = _whole(args.get("qty"), 1 if kind == Kind.USED else 0)
        what = "were used" if kind == Kind.USED else "are on the shelf"
        if qty is None:
            return f"How many {pluralize_unit(item.unit, 2)} of {item.name} {what}?"
        if _unusual(item, qty):
            return (f"{format_qty(qty, item.unit)} of {item.name} is far more than you usually have, "
                    "so I haven't logged it. What's the right number?")
    now = timezone.localtime()
    events = pending.setdefault(item.pk, [])
    before = _forecast_for(item, now, extra=events)
    if kind == Kind.USED and before.on_hand is None:
        # Log usage doesn't offer "Used 1" here either: there's no count to take it from.
        return (f"{item.name} hasn't been counted yet, so there's nothing to take {qty} off. "
                "Count it first: tap it on Log usage, then Set exact count.")
    events.append(StockEvent(kind=kind, qty=qty, created_at=now))
    after = _forecast_for(item, now, extra=events)

    amount = format_qty(qty, item.unit)
    change, summary, toast = {
        Kind.USED: (f"Used {amount}", f"used {amount} of {item.name}",
                    f"Logged: used {amount} of {item.name} · the manager will see it"),
        Kind.OUT: ("Used the last one", f"used the last {item.name}",
                   f"Logged: used the last {item.name} · the manager will see it"),
        Kind.LOW: ("Running low", f"{item.name} running low",
                   f"Logged: running low on {item.name} · the manager will see it"),
        Kind.COUNT: (f"Count {amount}", f"counted {item.name} at {amount}",
                     f"Counted {item.name}: {amount} · the forecast is updated"),
    }[kind]
    return {
        "item": item.pk, "kind": kind, "qty": qty, "name": item.name, "change": change, "summary": summary,
        "toast": toast, "many": "Logged {n} changes · the manager will see it",
        "before": before.on_hand, "after": after.on_hand,
        "unit": pluralize_unit(item.unit, after.on_hand), "was": _chip(before), "now": _chip(after),
        "flag": _flag(item, before, after),
    }


def _apply_event(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    event, _ = log_event(user, item, line["kind"], line["qty"], client_id=client_id)
    return event.pk


def _event_action(name, kind, description, *, qty=None):
    properties = {"item": {"type": "STRING", "description": (
        "The item's name as written in the practice data. If more than one item fits what they said, "
        "pass what they said as it is: StockRoom asks which.")}}
    if qty:
        properties["qty"] = {"type": "INTEGER", "description": qty}
    return Action(
        name=name,
        roles=ANYONE,
        declaration={
            "description": description,
            "parameters": {"type": "OBJECT", "properties": properties, "required": list(properties)},
        },
        preview=lambda user, args, pending: _event_line(user, args, pending, kind),
        apply=_apply_event,
    )


# --- Receive a delivery: Deliveries' Receive and Receive all, through the same receive_delivery_line ---

DELIVERIES = "stock:deliveries"


def _nothing_on_order(what):
    """No open order: say so and offer Deliveries. An order is never made up."""
    return Answer(f"There's no open order for {what}, so there's nothing to receive against.",
                  [_button("Open Deliveries", reverse(DELIVERIES))])


def _supplier_for(user, args):
    """The practice's supplier named: a Supplier, a Which when several fit, or a plain answer."""
    name = str(args.get("supplier") or "").strip()
    if not name:
        return "Which supplier was it?"
    suppliers = list(Supplier.objects.for_org(user.organisation).filter(is_active=True).order_by("name"))
    if "supplier" in args["picks"]:
        return next((s for s in suppliers if s.pk == args["picks"]["supplier"]), "That supplier isn't set up any more.")
    words = set(synonym_key(name).split())
    fits = [s for s in suppliers if s.name.lower() == name.lower()] or [
        s for s in suppliers if words and words <= set(synonym_key(s.name).split())
    ]
    if not fits:
        return f"I can't find a supplier called '{name}'."
    if len(fits) > 1:
        return Which(name, [{"id": s.pk, "name": s.name} for s in fits[:MAX_OPTIONS]], kind="supplier")
    return fits[0]


def _price(value):
    """A price from the model, in dollars to the cent, or None when it isn't one."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        price = Decimal(str(value).strip().lstrip("$")).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return price if 0 < price < 10000 else None


def _forecast_receiving(item, now, events, state):
    """The item's forecast with the receipts earlier lines make: their events,
    their order lines closed, and the back-order a part delivery leaves."""
    open_orders = [o for o in item.order_lines.all() if o.is_open and o.pk not in state["closed"]] + state["added"]
    return forecast([*item.events.all(), *events], open_orders, lead_days=item.supplier.lead_days,
                    order_size=item.order_size, now=now)


def _receipt_line(user, order, qty, price, pending):
    """One order line's card line: ordered, receiving, on hand before and after,
    and what stays on order. A manager's price is shown against the current one;
    an assistant's lines carry no price at all."""
    item, now = order.item, timezone.localtime()
    events = pending.setdefault(item.pk, [])
    state = pending.setdefault(("orders", item.pk), {"closed": set(), "added": []})
    before = _forecast_receiving(item, now, events, state)
    events.append(StockEvent(kind=Kind.RECEIVED, qty=qty, created_at=now))
    state["closed"].add(order.pk)
    left = max(order.qty - qty, 0)  # an invoice can deliver more than was ordered
    if left:
        state["added"].append(SimpleNamespace(qty=left))
    after = _forecast_receiving(item, now, events, state)
    pending.setdefault("received", set()).add(order.pk)

    amount = format_qty(qty, item.unit)
    flag = [f"{format_qty(left, item.unit)} stay on order, as a back-order."] if left else []
    if _flag(item, before, after):
        flag.append(_flag(item, before, after))
    line = {
        "item": item.pk, "order_line": order.pk, "qty": qty, "name": item.name,
        "change": f"Ordered {order.qty}, receiving {qty}", "summary": f"received {amount} of {item.name}",
        "toast": f"Received {amount} of {item.name} from {order.supplier.name}", "many": "Received {n} items",
        "before": before.on_hand, "after": after.on_hand, "unit": pluralize_unit(item.unit, after.on_hand),
        "was": _chip(before), "now": _chip(after), "flag": " ".join(flag),
    }
    if price is not None and user.is_org_admin:
        line["price"] = str(price)
        line["price_note"] = (f"Price ${price}, was ${item.price}" if item.price is not None and item.price != price
                              else f"Price ${price}")
        if invoices.price_needs_confirming(item.price, price):
            line["price_note"] += f": up {round((price - item.price) / item.price * 100)}%. Check it's right."
    return line


def _receive_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    orders = list(_open_lines_for_org(user.organisation).filter(item=item).exclude(pk__in=pending.get("received", ())))
    if not orders:
        return _nothing_on_order(item.name)
    if len(orders) > 1 and "line" not in args["picks"]:
        return Which(item.name, [
            {"id": o.pk, "name": f"{o.qty} from {o.supplier.name}, ordered {timezone.localtime(o.ordered_at):%a %d %b}"}
            for o in orders[:MAX_OPTIONS]
        ], kind="line")
    order = next((o for o in orders if o.pk == args["picks"].get("line", orders[0].pk)), None)
    if order is None:
        return "That order isn't open any more."
    qty = order.qty if args.get("qty") is None else _whole(args["qty"], 1)
    if qty is None or qty > order.qty:
        return (f"{format_qty(order.qty, item.unit)} of {item.name} were ordered. "
                f"How many arrived, from 1 to {order.qty}?")
    price = None
    if user.is_org_admin and args.get("price") is not None:
        price = _price(args["price"])
        if price is None:
            return f"What did they charge for one {item.unit} of {item.name}?"
    return _receipt_line(user, order, qty, price, pending)


def _receive_order(user, args, pending):
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    open_lines = [o for o in _open_lines_for_org(user.organisation).filter(supplier=supplier)
                  if o.pk not in pending.get("received", ())]
    if not open_lines:
        return _nothing_on_order(supplier.name)
    # One order is what was ordered together: its reference, or else its day.
    orders = {}
    for order in open_lines:
        orders.setdefault(order.order_ref or str(timezone.localtime(order.ordered_at).date()), []).append(order)
    if len(orders) > 1:
        wanted = args["picks"].get("order")
        chosen = next((group for group in orders.values() if any(o.pk == wanted for o in group)), None)
        if chosen is None:
            return Which(supplier.name, [
                {"id": group[0].pk, "name": (f"Ordered {timezone.localtime(group[0].ordered_at):%a %d %b}, "
                                             f"{len(group)} item{'s' if len(group) != 1 else ''}")}
                for group in list(orders.values())[:MAX_OPTIONS]
            ], kind="order")
        open_lines = chosen
    return [_receipt_line(user, order, order.qty, None, pending) for order in open_lines]


def _apply_receipt(user, line, client_id):
    order = _open_lines_for_org(user.organisation).get(pk=line["order_line"])
    price = Decimal(line["price"]) if user.is_org_admin and line.get("price") else None
    received = receive_delivery_line(user, order, line["qty"], price)
    if received is None:  # more than is on order now
        raise ObjectDoesNotExist
    return f"{order.pk}:{received[0].pk}"


def _receive_line_declaration(user):
    properties = {
        "item": {"type": "STRING", "description": (
            "The item's name as written in the practice data. If more than one item fits what they said, "
            "pass what they said as it is: StockRoom asks which.")},
        "qty": {"type": "INTEGER", "description": "How many arrived, only if they said it was fewer than ordered."},
    }
    if user.is_org_admin:
        properties["price"] = {"type": "NUMBER", "description": (
            "What they were charged for one, in dollars, only if they said.")}
    return {
        "description": ("Receive one item of an order that has arrived, e.g. 'the composite's here' or "
                        "'only 3 of the 5 boxes of gloves arrived'. What's short stays on order as a back-order."),
        "parameters": {"type": "OBJECT", "properties": properties, "required": ["item"]},
    }


# --- An invoice or receipt dropped into the chat: the upload page's read, match and Confirm ---
# It's never an action Gemini can call. The file is read by stock.invoices.parse_invoice
# alone, which has nothing to call, and nothing read off it goes into the card's summary
# (the chat's record, sent back to the AI with later questions): only the practice's own
# item and supplier names, and counts. Assistants can send one too (whoever unpacks the box):
# their card has items and quantities only, and item prices wait for a manager. The reading
# itself, prices and all, stays on the server in the session until Confirm saves it, as the
# upload page's preview does; it's never rendered or sent to the AI.

RECEIPT_ROLES = ANYONE


def propose_receipt(user, invoice, lines):
    """The card for a read invoice: each line it would receive against what's
    on order, as ingest() will, then the lines that need checking, which it
    leaves alone. A plain answer if there's nothing on it to show."""
    if not lines:
        return ("StockRoom couldn't find any products on that. A clearer photo usually does it: flat, in good "
                "light, with the whole invoice in frame.")
    invoices.match_lines(invoice, lines)
    invoices.match_orders(invoice, lines)
    # Only what passes check_invoice (a supplier of the practice's, lines that add up) is received.
    receivable = invoice.status == Invoice.Status.PARSED
    pending, shown, unmatched = {}, [], []
    for line in lines:
        if receivable and line.order and line.qty:
            price = line.unit_price if user.is_org_admin else None
            card_line = _receipt_line(user, line.order, line.qty, price, pending)
            # Shorter than a delivery's card, so a long invoice scans: the chips already show a status change.
            if line.qty == line.order.qty:
                card_line["change"] = f"Receiving {line.qty}"
            card_line["flag"] = card_line["flag"].replace(f"Its status changes to {card_line['now']['status_label']}.", "")
            shown.append({"action": "receive_invoice", **card_line})
        else:
            why = ("" if not receivable else "No quantity on it" if not line.qty
                   else f"{line.item.name}, but it isn't on order" if line.item else "Nothing in your stock matches it")
            unmatched.append({"name": line.description or line.sku, "qty": line.qty, "why": why})

    supplier = invoice.supplier.name if invoice.supplier else "a supplier StockRoom doesn't know"
    if shown:
        received = [line["summary"] for line in shown]
        summary = ", ".join(received[:-1]) + (" and " if len(received) > 1 else "") + received[-1] + f" from {supplier}"
    else:
        summary = f"saved an invoice from {supplier}"
    if unmatched:
        summary += f", with {len(unmatched)} line{'s' if len(unmatched) != 1 else ''} to check"
    problems = []
    if not invoice.supplier:
        problems.append(f"There's no supplier called \"{invoice.supplier_name}\"" if invoice.supplier_name
                        else "It doesn't say who it's from")
    if not invoice.totals_ok:
        problems.append("some lines don't add up")
    note = ""
    if problems:
        note = " and ".join(problems) + ", so nothing can be received from here. Confirm saves it to check" + (
            "." if user.is_org_admin else " for the manager.")
        note = note[0].upper() + note[1:]
    return {
        "calls": [], "picks": {}, "which": None, "lines": shown, "unmatched": unmatched, "note": note,
        "summary": summary, "receipt": pending_invoice(invoice, lines),
        "head": {"supplier": invoice.supplier.name if invoice.supplier else invoice.supplier_name or "No supplier shown",
                 "number": invoice.invoice_number,
                 "date": f"{invoice.issued_on.day} {invoice.issued_on:%b %Y}" if invoice.issued_on else ""},
    }


def _apply_receipt_card(user, card):
    """Confirm on a receipt card: the invoice page's Confirm, with nothing changed
    on it. A manager's card showed each price change, so Confirm is the tick for
    them. An assistant's receives the stock and leaves item prices for a manager
    (stock.invoices.receive), who sees it under invoices to check."""
    if user.role not in RECEIPT_ROLES:
        return None
    org = user.organisation
    invoice = Invoice(organisation=org, brought_in_by=None if user.is_org_admin else user)
    lines = fill_invoice(org, invoice, card["receipt"], QueryDict())
    for line in lines:
        line.price_confirmed = True
    invoice = invoices.ingest(invoice, lines, user)
    card["invoice_id"] = invoice.pk
    received = sum(1 for line in lines if line.order_line_id)
    text = {
        Invoice.Status.IGNORED: "That one's been brought in already, so nothing changed.",
        Invoice.Status.CONFLICT: "Saved, but it needs checking before it counts.",
        Invoice.Status.COMPLETE: "Received everything on the invoice.",
        Invoice.Status.PARTIAL: f"Received {received} of {len(lines)} lines.",
    }.get(invoice.status, "Invoice saved. Nothing on it was received, so open it to add the lines.")
    if not user.is_org_admin and invoice.status != Invoice.Status.IGNORED:
        text = text.replace(", so open it to add the lines.", ".") + " The manager will check it."
    return text, reverse("stock:invoice_undo", args=[invoice.pk]) if invoice.undo_snapshot else ""


# --- Ordering: the Reorder list's buttons, through the same functions in stock.views ---
# Managers add to and take off the list, set an order size, mark lines or a supplier's
# lines ordered and switch to a backup supplier, and get the order email. An assistant
# can only ask for an item to be added (the page's own request flow), so the
# manager-only actions are never in the declarations an assistant's request carries.

def _static(item, f, change, summary, toast, many, detail="", **extra):
    """A card line for a change that leaves on hand alone: shown with its status
    chip rather than before and after."""
    return {
        "item": item.pk, "name": item.name, "static": True, "change": change, "summary": summary, "toast": toast,
        "many": many, "detail": detail, "before": f.on_hand, "after": f.on_hand,
        "unit": pluralize_unit(item.unit, f.on_hand), "was": _chip(f), "now": _chip(f), "flag": "", **extra,
    }


def _plain(name, change, summary, toast, many, detail="", **extra):
    """A card line with nothing to show for on hand: a new item, or a supplier."""
    return {"name": name, "static": True, "bare": True, "change": change, "summary": summary, "toast": toast,
            "many": many, "detail": detail, "flag": "", **extra}


def _reorder_button():
    return _button("Open the Reorder list", reverse("stock:reorder_list"))


def _add_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    f = _forecast_for(item, timezone.localtime())
    listed = pending.setdefault("listed", set())
    if f.status in WANTED_STATUSES or item.pinned_to_reorder_at or item.pk in listed:
        return f"{item.name} is already on the reorder list."
    if not user.is_org_admin and item.reorder_requested_by_id:
        return f"{item.name} has already been asked for. The manager will see it."
    listed.add(item.pk)
    if user.is_org_admin:
        return _static(
            item, f, "Add to the reorder list", f"added {item.name} to the reorder list",
            f"Added {item.name} to the reorder list.", "Added {n} items to the reorder list",
            flag="This puts it on the reorder list.", old={})
    return _static(
        item, f, "Ask the manager to order it", f"asked the manager to order {item.name}",
        f"Asked the manager to add {item.name}.", "Asked the manager to add {n} items",
        "Nothing is ordered. The manager will see it at the top of their reorder list.", old={})


def _apply_add(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    add_to_reorder_list(user, item)
    return item.pk


def _add_declaration(user):
    if user.is_org_admin:
        what = "Put an item on the reorder list, e.g. 'add gloves to the reorder list'."
    else:
        what = ("Ask the manager to order more of an item, e.g. 'ask the manager to order more suction tips' or "
                "'we need more gloves ordered'. Nothing is ordered: the manager sees the request on their reorder list.")
    return {
        "description": f"{what} Call it once for each item.",
        "parameters": {"type": "OBJECT", "properties": {"item": {"type": "STRING", "description": (
            "The item's name as written in the practice data. If more than one item fits what they said, "
            "pass what they said as it is: StockRoom asks which.")}}, "required": ["item"]},
    }


def _remove_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    f = _forecast_for(item, timezone.localtime())
    asked = item.reorder_requested_by
    pinned = bool(item.pinned_to_reorder_at)
    if not pinned and not asked:
        if f.status in WANTED_STATUSES:
            return (f"{item.name} is on the reorder list because of its stock ({f.status.label}), so it stays "
                    "until it's ordered. Tell me when you've ordered it and I'll mark it ordered.")
        return f"{item.name} isn't on the reorder list."
    if item.pk in pending.setdefault("removed", set()):
        return f"{item.name} is already in this change."
    pending["removed"].add(item.pk)
    why = " and ".join(([f"{asked.name or 'Someone'} asked for it"] if asked else []) + (["you added it"] if pinned else []))
    return _static(
        item, f, "Take off the reorder list", f"took {item.name} off the reorder list",
        f"Took {item.name} off the reorder list.", "Took {n} items off the reorder list", why[0].upper() + why[1:] + ".",
        flag="It stays on the list until it's ordered, because it's running low." if f.status in WANTED_STATUSES else "",
        old={"pinned": pinned, "asked_by": item.reorder_requested_by_id})


def _apply_remove(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    _off_the_list(item)
    return item.pk


def _restore_remove(user, line):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"])
    item.pinned_to_reorder_at = timezone.now() if line["old"]["pinned"] else None
    item.reorder_requested_by_id = line["old"]["asked_by"]
    item.save(update_fields=["pinned_to_reorder_at", "reorder_requested_by"])


def _size_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    qty = _whole(args.get("qty"), 1)
    if qty is None:
        return f"How many {pluralize_unit(item.unit, 2)} of {item.name} should it order at a time?"
    new = format_qty(qty, item.unit)
    if item.order_size == qty:
        return f"{item.name} is already ordered {new} at a time."
    if item.pk in pending.setdefault("sized", set()):
        return f"{item.name} is already in this change."
    pending["sized"].add(item.pk)
    old = format_qty(item.order_size, item.unit) if item.order_size else "not set"
    return _static(
        item, _forecast_for(item, timezone.localtime()), f"Order {new} at a time",
        f"set {item.name} to be ordered {new} at a time", f"{item.name} will be ordered {new} at a time.",
        "Changed {n} order sizes", f"Order size: {old} → {new}", qty=qty, old={"order_size": item.order_size})


def _apply_size(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    set_order_size(item, line["qty"])
    return item.pk


def _restore_size(user, line):
    set_order_size(Item.objects.for_org(user.organisation).get(pk=line["item"]), line["old"]["order_size"])


def _order_line(item, f, qty, now):
    """A card line for ordering qty of item from its preferred supplier, with the
    status it'll have, what's already on its way and (managers only) the cost."""
    after = _forecast_receiving(item, now, [], {"closed": set(), "added": [SimpleNamespace(qty=qty)]})
    supplier, amount = item.supplier, format_qty(qty, item.unit)
    on_order = sum(o.qty for o in item.order_lines.all() if o.is_open)
    cost = qty * Decimal(str(item.price)) if item.price is not None else None
    line = _static(
        item, f, f"Order {amount}", f"ordered {amount} of {item.name} from {supplier.name}",
        f"Ordered {amount} of {item.name} from {supplier.name}", "Ordered {n} items",
        f"From {supplier.name}, about {supplier.lead_days} days", qty=qty, after=after.on_hand, now=_chip(after),
        flag=f"{format_qty(on_order, item.unit)} already on order. This adds more." if on_order else "",
        cost="" if cost is None else str(cost))
    if cost is not None:
        line["price_note"] = f"{qty} × ${Decimal(str(item.price)):.2f} = ${cost:.2f}"
    return line


def _ordered_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    ordered = pending.setdefault("ordered", set())
    if item.pk in ordered:
        return f"{item.name} is already in this order."
    now = timezone.localtime()
    f = _forecast_for(item, now)
    qty = _default_qty(item, f) if args.get("qty") is None else _whole(args["qty"], 1)
    if qty is None:
        return f"How many {pluralize_unit(item.unit, 2)} of {item.name} did you order?"
    ordered.add(item.pk)
    return _order_line(item, f, qty, now)


def _supplier_order(user, args, pending):
    """Everything on the reorder list from one supplier, at the quantities its
    Mark all ordered button would use."""
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    now = timezone.localtime()
    ordered = pending.setdefault("ordered", set())
    wanted = [(item, f) for item, f in _wanted_items(user.organisation, now)
              if item.supplier_id == supplier.pk and item.pk not in ordered]
    if not wanted:
        return Answer(f"Nothing on the reorder list is from {supplier.name}.", [_reorder_button()])
    lines = []
    for item, f in sorted(wanted, key=lambda entry: entry[0].name):
        ordered.add(item.pk)
        lines.append(_order_line(item, f, _default_qty(item, f), now) | {
            "many": f"Ordered {{n}} items from {supplier.name}"})
    return lines


def _apply_order(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    return place_order(user, item, line["qty"]).pk


def _switch_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    if supplier.pk == item.supplier_id:
        return f"{item.name} already comes from {supplier.name}."
    if supplier.pk not in {s.pk for s in _backups(item)}:
        return Answer(f"{supplier.name} isn't set up as a backup for {item.name}. Add it on the item's page "
                      "first, then ask again.",
                      [_button(f"Open {item.name}", reverse("stock:item_detail", args=[item.pk]))])
    old = item.supplier
    return _static(
        item, _forecast_for(item, timezone.localtime()), f"Order from {supplier.name}",
        f"switched {item.name} to {supplier.name}",
        f"{item.name} now comes from {supplier.name}. {old.name} is kept as a backup.", "Switched {n} suppliers",
        f"Supplier: {old.name} → {supplier.name}, about {supplier.lead_days} days. {old.name} is kept as a backup.",
        supplier=supplier.pk, old={"supplier": old.pk})


def _apply_switch(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    switch_supplier(item, item.other_suppliers.filter(is_active=True).get(pk=line["supplier"]))
    return item.pk


def _restore_switch(user, line):
    org = user.organisation
    switch_supplier(Item.objects.for_org(org).get(pk=line["item"]),
                    Supplier.objects.for_org(org).get(pk=line["old"]["supplier"]))


def _order_email(user, args, pending):
    """The email the Reorder list writes for a supplier, with its Email and Call
    buttons. Sending stays a tap in the manager's own mail app."""
    supplier = _supplier_for(user, args)
    if isinstance(supplier, Which):
        return "Which supplier did you mean: " + " or ".join(o["name"] for o in supplier.options) + "?"
    if not isinstance(supplier, Supplier):
        return supplier
    now = timezone.localtime()
    group = next((g for g in _reorder_groups(user.organisation, user, _wanted_items(user.organisation, now), now.date())
                  if g["supplier"].pk == supplier.pk), None)
    if group is None:
        return Answer(f"Nothing on the reorder list is from {supplier.name}, so there's no order to write.",
                      [_reorder_button()])
    buttons = []
    if group["mailto"]:
        intro = f"To: {supplier.email}\n" + group["email"]
        buttons.append(_button(f"Email {supplier.name}", group["mailto"]))
    else:
        intro = (f"{supplier.name} has no email address set up, so here's the order to read out or paste in.\n\n"
                 + group["email"])
    if group["phone_digits"]:
        buttons.append(_button(f"Call {supplier.name}", "tel:" + group["phone_digits"]))
    return Answer(intro, buttons)


def _supplier_property(what):
    return {"type": "STRING", "description": f"{what} The supplier's name as written in the practice data."}


def _manager_action(name, description, preview, apply, properties, *, required, restore=None, roles=ADMIN, **kwargs):
    undo = {"undo": RESTORE} if restore else {}
    return Action(
        name, roles,
        {"description": description, "parameters": {"type": "OBJECT", "properties": properties,
                                                      "required": required}},
        preview, apply, refusal=MANAGER_JOB, restore=restore, **undo, **kwargs)


ITEM_PROPERTY = {"type": "STRING", "description": (
    "The item's name as written in the practice data. If more than one item fits what they said, "
    "pass what they said as it is: StockRoom asks which.")}


# --- Items, prices and suppliers: the Stock and Suppliers pages' forms and buttons, managers only ---
# A new or changed item or supplier goes through the page's own form (ItemForm, SupplierForm),
# so the same fields are checked the same way. A card line keeps the old values it would
# change, so Undo can put them back; what's new is found again by its id, set when it's made.

def _error(form):
    return str(next(iter(form.errors.values()))[0])


def _clean(value, limit=200):
    return str(value).strip()[:limit] if isinstance(value, str | int | float) and not isinstance(value, bool) else ""


def _open_button(item):
    return _button(f"Open {item.name}", reverse("stock:item_detail", args=[item.pk]))


def _item_data(item):
    """What ItemForm would be sent for this item as it is."""
    return {"name": item.name, "unit": item.unit, "supplier": item.supplier_id,
            "price": "" if item.price is None else str(item.price),
            "order_size": "" if item.order_size is None else item.order_size}


def _fit_archived(rows, name, kind, picks):
    """The archived row (item or supplier) that's called name, a Which when several fit, or a plain answer."""
    if kind in picks:
        return next((r for r in rows if r.pk == picks[kind]), f"That {kind} isn't archived any more.")
    if not name:
        return f"Which {kind} was it?"
    words = set(synonym_key(name).split())
    fits = [r for r in rows if r.name.lower() == name.lower()] or [
        r for r in rows if words and words <= set(synonym_key(r.name).split())]
    if not fits:
        return f"I can't find an archived {kind} called '{name}'."
    if len(fits) > 1:
        return Which(name, [{"id": r.pk, "name": r.name} for r in fits[:MAX_OPTIONS]], kind=kind)
    return fits[0]


def _catalogue_match(org, name):
    """The catalogue product this name is, if the practice doesn't stock it yet."""
    stocked, key = _stocked_names(org), synonym_key(name)
    best = (0.0, None)
    for product in CatalogueProduct.objects.prefetch_related("offers__supplier"):
        other = synonym_key(product.name)
        if normalize(product.name) in stocked or _sizes(key) != _sizes(other):
            continue
        if set(key.split()) < set(other.split()) or set(other.split()) < set(key.split()):
            continue  # "gloves" is like "Nitrile gloves, size M", but not surely it: the same rule as the matcher
        best = max(best, (similarity(key, other), product), key=lambda pair: pair[0])
    return best[1] if best[0] >= SIMILAR else None


def _money(price):
    return f"${price:.2f}"


def _new_item(user, args, pending):
    """A new item. Asked first when it looks like one already in stock, or one in the
    catalogue (which has the unit and who sells it); never guessed either way."""
    org, picks = user.organisation, args["picks"]
    name, unit = _clean(args.get("name")), _clean(args.get("unit"), 50).lower()
    if not name:
        return "What's the item called?"
    if "dup" in picks:
        if picks["dup"]:  # "Yes, that one"
            same = Item.objects.for_org(org).filter(pk=picks["dup"]).first()
            return Answer(f"Okay, nothing added. {same.name} is already in your stock.", [_open_button(same)])
    else:
        found = Matcher(org).match(name)
        if found.band in ("sure", "likely"):
            return Which(name, [{"id": found.item.pk, "name": f"Yes, {found.item.name}"},
                                {"id": 0, "name": "No, it's a different one"}], kind="dup",
                         ask=f"You already have {found.item.name}. Did you mean that one?")
    given = _clean(args.get("supplier"))
    price = _price(args.get("price")) if args.get("price") is not None else None
    if args.get("price") is not None and price is None:
        return f"What's the price of one {unit or 'unit'} of {name}?"
    size = _whole(args.get("order_size"), 1) if args.get("order_size") is not None else None
    if args.get("order_size") is not None and size is None:
        return f"How many of {name} do you order at a time?"

    product = None
    if "catalogue" in picks:
        if picks["catalogue"]:
            product = CatalogueProduct.objects.prefetch_related("offers__supplier").filter(pk=picks["catalogue"]).first()
    elif product := _catalogue_match(org, name):
        offered = [n for n, _ in _supplier_choices(org, product)]
        if not given or given.lower() in (n.lower() for n in offered):
            return Which(name, [{"id": product.pk, "name": f"Add {product.name} from the catalogue"},
                                {"id": 0, "name": f"No, add {name} as I said"}], kind="catalogue",
                         ask=f"{product.name} is in the catalogue. Add that one?")
        product = None
    if product:
        if normalize(product.name) in _stocked_names(org):
            return f"{product.name} is already on your stock list."
        choices = _supplier_choices(org, product)
        chosen, ours = next(((n, o) for n, o in choices if n.lower() == given.lower()),
                            next(((n, o) for n, o in choices if o), choices[0]))
        detail = [f"From the catalogue: {product.unit}", f"Supplier: {chosen}"]
        if ours is None:
            detail.append(f"{chosen} isn't one of your suppliers yet, so it's added as one.")
        if price:
            detail.append(f"Price: {_money(price)} a {product.unit}")
        if size:
            detail.append(f"Order size: {size}")
        return _plain(product.name, f"Add {product.name}", f"added {product.name}", f"Added {product.name}.",
                      "Added {n} items", "\n".join(detail), product=product.pk, chosen=chosen,
                      price=str(price) if price else "", order_size=size or 0)

    if not unit:
        return f"What's {name} counted in? For example box, syringe or pack."
    if not given and "supplier" not in picks:
        return f"Who do you order {name} from?"
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    new = {"name": name, "unit": unit, "supplier": supplier.pk, "price": str(price) if price else "",
           "order_size": size or ""}
    form = ItemForm(new, instance=Item(organisation=org))
    if not form.is_valid():
        return _error(form)
    detail = [f"Counted in {pluralize_unit(unit, 2)}", f"Supplier: {supplier.name}"]
    if price:
        detail.append(f"Price: {_money(price)} a {unit}")
    if size:
        detail.append(f"Order size: {size}")
    return _plain(name, f"Add {name}", f"added {name}", f"Added {name}.", "Added {n} items", "\n".join(detail),
                  new=new)


def _apply_new_item(user, line, client_id):
    org = user.organisation
    if line.get("product"):
        product = CatalogueProduct.objects.get(pk=line["product"])
        if normalize(product.name) in _stocked_names(org) or line["chosen"] not in dict(_supplier_choices(org, product)):
            raise ObjectDoesNotExist
        item, _ = add_from_catalogue(org, product, line["chosen"])
        if line["price"]:
            set_item_price(item, Decimal(line["price"]))
        if line["order_size"]:
            set_order_size(item, line["order_size"])
        return item.pk
    form = ItemForm(line["new"], instance=Item(organisation=org))
    if not form.is_valid():
        raise ObjectDoesNotExist
    return form.save().pk


def _restore_new(user, line):
    """Undo of an add: gone if nothing's been done with it, otherwise archived, as the page's Undo would."""
    item = Item.objects.for_org(user.organisation).get(pk=line["made"])
    if item.events.exists() or item.order_lines.exists():
        set_item_active(item, False)
    else:
        item.delete()


def _edit_item(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    org, picks = user.organisation, args["picks"]
    name, unit = _clean(args.get("new_name")), _clean(args.get("new_unit"), 50).lower()
    wants_supplier = bool(_clean(args.get("new_supplier"))) or "supplier" in picks
    new, old, detail = {}, {}, []
    if name and name != item.name:
        other = Matcher(org).match(name)
        if other.band in ("sure", "likely") and other.item.pk != item.pk:
            return f"You already have {other.item.name}, so I haven't renamed {item.name}."
        new["name"], old["name"] = name, item.name
        detail.append(f"Name: {item.name} → {name}")
    if unit and unit != item.unit:
        new["unit"], old["unit"] = unit, item.unit
        detail.append(f"Unit: {item.unit} → {unit}")
    if wants_supplier:
        supplier = _supplier_for(user, {**args, "supplier": args.get("new_supplier")})
        if not isinstance(supplier, Supplier):
            return supplier
        if supplier.pk != item.supplier_id:
            new["supplier"] = supplier.pk
            old["supplier"] = item.supplier_id
            old["backup"] = item.other_suppliers.filter(pk=supplier.pk).exists()
            detail.append(f"Supplier: {item.supplier.name} → {supplier.name}")
    if not new:
        return f"There's nothing to change on {item.name}. Tell me what it should be called, counted in or ordered from."
    f = _forecast_for(item, timezone.localtime())
    form = ItemForm(_item_data(item) | new, instance=Item(organisation=org, pk=item.pk))
    if not form.is_valid():
        return _error(form)
    what = " and ".join(["name", "unit", "supplier"][i] for i, k in enumerate(("name", "unit", "supplier")) if k in new)
    return _static(
        item, f, f"Change its {what}", f"changed the {what} of {item.name}", f"Changed {item.name}.",
        "Changed {n} items", "\n".join(detail), new=new, old=old,
        flag=("Its backups stay as they are." if "supplier" in new else ""))


def _apply_edit(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    form = ItemForm(_item_data(item) | line["new"], instance=item)
    if not form.is_valid():
        raise ObjectDoesNotExist
    item = form.save()
    item.other_suppliers.remove(item.supplier)  # now the preferred one, so no longer a backup
    return item.pk


def _restore_edit(user, line):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"])
    fields = {k: v for k, v in line["old"].items() if k != "backup"}
    for field, value in fields.items():
        setattr(item, "supplier_id" if field == "supplier" else field, value)
    item.save(update_fields=list(fields))
    if line["old"].get("backup"):
        item.other_suppliers.add(line["new"]["supplier"])


def _price_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    price = _price(args.get("price"))
    if price is None:
        return f"What's the price of one {item.unit} of {item.name}?"
    old = None if item.price is None else Decimal(str(item.price))
    if old == price:
        return f"{item.name} is already {_money(price)} a {item.unit}."
    flag = ""
    if invoices.price_needs_confirming(old, price):
        flag = f"That's up {round((price - old) / old * 100)}% on the price now. Check it's right."
    return _static(
        item, _forecast_for(item, timezone.localtime()), f"Price {_money(price)}",
        f"set the price of {item.name} to {_money(price)}",
        f"{item.name} is now {_money(price)} a {item.unit}.", "Changed {n} prices",
        f"Price: {'not set' if old is None else _money(old)} → {_money(price)} a {item.unit}",
        price=str(price), old={"price": None if old is None else str(old)}, flag=flag)


def _apply_price(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    set_item_price(item, Decimal(line["price"]))
    return item.pk


def _restore_price(user, line):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"])
    set_item_price(item, None if line["old"]["price"] is None else Decimal(line["old"]["price"]))


def _archive_line(user, args, pending):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    on_order = sum(o.qty for o in item.order_lines.all() if o.is_open)
    return _static(
        item, _forecast_for(item, timezone.localtime()), "Archive it", f"archived {item.name}",
        f"Archived {item.name}.", "Archived {n} items",
        "It won't show on Log usage or the reorder list. You can bring it back with Undo, or by asking me.",
        flag=f"{format_qty(on_order, item.unit)} still on order, which you can receive as usual." if on_order else "")


def _apply_archive(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    set_item_active(item, False)
    return item.pk


def _restore_archive(user, line):
    set_item_active(Item.objects.for_org(user.organisation).get(pk=line["item"]), True)


def _unarchive_line(user, args, pending):
    rows = Item.objects.for_org(user.organisation).filter(is_active=False).order_by("name")
    item = _fit_archived(rows, _clean(args.get("item")), "item", args["picks"])
    if not isinstance(item, Item):
        return item
    return _static(
        item, _forecast_for(item, timezone.localtime()), "Bring it back", f"brought back {item.name}",
        f"{item.name} is back.", "Brought back {n} items", "It will show on Log usage and the reorder list again.")


def _apply_unarchive(user, line, client_id):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=False)
    set_item_active(item, True)
    return item.pk


def _restore_unarchive(user, line):
    set_item_active(Item.objects.for_org(user.organisation).get(pk=line["item"]), False)


def _backup_line(user, args, pending, *, add):
    item = _item_for(user, args)
    if not isinstance(item, Item):
        return item
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    is_backup = item.other_suppliers.filter(pk=supplier.pk).exists()
    if supplier.pk == item.supplier_id:
        return f"{supplier.name} is already who {item.name} comes from."
    if add and is_backup:
        return f"{supplier.name} is already a backup for {item.name}."
    if not add and not is_backup:
        return f"{supplier.name} isn't a backup for {item.name}."
    verb = "Add" if add else "Remove"
    return _static(
        item, _forecast_for(item, timezone.localtime()), f"{verb} {supplier.name}",
        f"{'added' if add else 'removed'} {supplier.name} as a backup for {item.name}",
        f"{supplier.name} {'added as another supplier for' if add else 'removed from'} {item.name}"
        f"{'' if add else chr(39) + 's suppliers'}.", "Changed {n} backup suppliers",
        f"Backup suppliers: {verb.lower()} {supplier.name}. {item.supplier.name} stays the one it's ordered from.",
        supplier=supplier.pk)


def _apply_backup_add(user, line, client_id):
    org = user.organisation
    item = Item.objects.for_org(org).get(pk=line["item"], is_active=True)
    if not add_backup_supplier(item, Supplier.objects.for_org(org).get(pk=line["supplier"], is_active=True)):
        raise ObjectDoesNotExist
    return item.pk


def _apply_backup_remove(user, line, client_id):
    org = user.organisation
    item = Item.objects.for_org(org).get(pk=line["item"], is_active=True)
    item.other_suppliers.remove(item.other_suppliers.get(pk=line["supplier"]))
    return item.pk


def _restore_backup_add(user, line):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"])
    item.other_suppliers.remove(line["supplier"])


def _restore_backup_remove(user, line):
    item = Item.objects.for_org(user.organisation).get(pk=line["item"])
    item.other_suppliers.add(line["supplier"])


def _new_supplier(user, args, pending):
    org, picks = user.organisation, args["picks"]
    name = _clean(args.get("name"))
    if not name:
        return "What's the supplier called?"
    rows = list(Supplier.objects.for_org(org))
    if "dup" in picks:
        if picks["dup"]:
            same = next((s for s in rows if s.pk == picks["dup"]), None)
            return Answer(f"Okay, nothing added. {same.name} is already one of your suppliers"
                          f"{'' if same.is_active else ', archived. Ask me to bring it back'}.",
                          [_button("Open Suppliers", reverse("stock:suppliers"))])
    elif fits := _similar(name, rows):
        same = fits[0]
        return Which(name, [{"id": same.pk, "name": f"Yes, {same.name}"}, {"id": 0, "name": "No, it's a different one"}],
                     kind="dup", ask=f"You already have {same.name}"
                                     f"{'' if same.is_active else ' (archived)'}. Did you mean that one?")
    days = Supplier._meta.get_field("lead_days").get_default() if args.get("lead_days") is None \
        else _whole(args["lead_days"], 1)
    if days is None or days > 60:
        return f"How many days does {name} take to deliver, from 1 to 60?"
    new = {"name": name, "lead_days": days, "phone": _clean(args.get("phone"), 50),
           "email": _clean(args.get("email"))}
    form = SupplierForm(new, instance=Supplier(organisation=org))
    if not form.is_valid():
        return _error(form)
    detail = [f"Delivers in about {days} days" + ("" if args.get("lead_days") is not None else " (you can change that)")]
    detail += [f"{label}: {value}" for label, value in (("Phone", new["phone"]), ("Email", new["email"])) if value]
    return _plain(name, f"Add {name}", f"added {name} as a supplier", f"Added {name}.", "Added {n} suppliers",
                  "\n".join(detail), new=new)


def _apply_new_supplier(user, line, client_id):
    form = SupplierForm(line["new"], instance=Supplier(organisation=user.organisation))
    if not form.is_valid():
        raise ObjectDoesNotExist
    return form.save().pk


def _restore_new_supplier(user, line):
    supplier = Supplier.objects.for_org(user.organisation).get(pk=line["made"])
    if supplier.items.exists() or supplier.order_lines.exists():
        set_supplier_active(supplier, False)
    else:
        supplier.delete()


def _supplier_data(supplier):
    return {"name": supplier.name, "lead_days": supplier.lead_days, "phone": supplier.phone, "email": supplier.email}


def _edit_supplier(user, args, pending):
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    new, old, detail = {}, {}, []
    for field, label, value in (("name", "Name", _clean(args.get("new_name"))),
                                ("phone", "Phone", _clean(args.get("phone"), 50)),
                                ("email", "Email", _clean(args.get("email")))):
        if value and value != getattr(supplier, field):
            new[field], old[field] = value, getattr(supplier, field)
            detail.append(f"{label}: {old[field] or 'not set'} → {value}")
    if args.get("lead_days") is not None:
        days = _whole(args["lead_days"], 1)
        if days is None or days > 60:
            return f"How many days does {supplier.name} take to deliver, from 1 to 60?"
        if days != supplier.lead_days:
            new["lead_days"], old["lead_days"] = days, supplier.lead_days
            detail.append(f"Delivery time: {supplier.lead_days} days → {days} days")
    if not new:
        return f"There's nothing to change on {supplier.name}."
    form = SupplierForm(_supplier_data(supplier) | new, instance=Supplier(organisation=user.organisation, pk=supplier.pk))
    if not form.is_valid():
        return _error(form)
    return _plain(
        supplier.name, f"Change {supplier.name}", f"changed {supplier.name}'s details", f"Changed {supplier.name}.",
        "Changed {n} suppliers", "\n".join(detail), supplier=supplier.pk, new=new, old=old,
        flag="This changes when its items need ordering." if "lead_days" in new else "")


def _apply_edit_supplier(user, line, client_id):
    supplier = Supplier.objects.for_org(user.organisation).get(pk=line["supplier"], is_active=True)
    form = SupplierForm(_supplier_data(supplier) | line["new"], instance=supplier)
    if not form.is_valid():
        raise ObjectDoesNotExist
    return form.save().pk


def _restore_edit_supplier(user, line):
    supplier = Supplier.objects.for_org(user.organisation).get(pk=line["supplier"])
    for field, value in line["old"].items():
        setattr(supplier, field, value)
    supplier.save(update_fields=list(line["old"]))


def _archive_supplier_line(user, args, pending):
    supplier = _supplier_for(user, args)
    if not isinstance(supplier, Supplier):
        return supplier
    if count := archive_blocked_by(supplier):
        return Answer(f"{supplier.name} still has {count} active item{'s' if count != 1 else ''}, so it can't be "
                      "archived. Move them to another supplier first.",
                      [_button("Open Stock", reverse("stock:items"))])
    return _plain(
        supplier.name, "Archive it", f"archived {supplier.name}", f"Archived {supplier.name}.", "Archived {n} suppliers",
        "It won't show when you pick a supplier. You can bring it back with Undo, or by asking me.",
        supplier=supplier.pk)


def _apply_archive_supplier(user, line, client_id):
    supplier = Supplier.objects.for_org(user.organisation).get(pk=line["supplier"], is_active=True)
    if archive_blocked_by(supplier):
        raise ObjectDoesNotExist
    set_supplier_active(supplier, False)
    return supplier.pk


def _restore_archive_supplier(user, line):
    set_supplier_active(Supplier.objects.for_org(user.organisation).get(pk=line["supplier"]), True)


def _unarchive_supplier_line(user, args, pending):
    rows = Supplier.objects.for_org(user.organisation).filter(is_active=False).order_by("name")
    supplier = _fit_archived(rows, _clean(args.get("supplier")), "supplier", args["picks"])
    if not isinstance(supplier, Supplier):
        return supplier
    return _plain(
        supplier.name, "Bring it back", f"brought back {supplier.name}", f"{supplier.name} is back.",
        "Brought back {n} suppliers", "It will show when you pick a supplier again.", supplier=supplier.pk)


def _apply_unarchive_supplier(user, line, client_id):
    supplier = Supplier.objects.for_org(user.organisation).get(pk=line["supplier"], is_active=False)
    set_supplier_active(supplier, True)
    return supplier.pk


def _restore_unarchive_supplier(user, line):
    set_supplier_active(Supplier.objects.for_org(user.organisation).get(pk=line["supplier"]), False)


# --- Buttons under an answer: open a page or download a file. Nothing changes, so there's no card. ---

def _button(label, url, download=False):
    return {"label": label, "url": url, "download": download}


# What the model may open, by name: key -> (label, URL name, roles, what's there). It never supplies a URL.
# The views behind them keep their own role checks; the roles here only decide who's offered each.
PAGES = {
    "home": ("Home", "stock:home", ANYONE, "what to order today"),
    "log_usage": ("Log usage", "stock:log_usage", ANYONE, "log what's been used, running low or counted"),
    "reorder_list": ("Reorder list", "stock:reorder_list", ANYONE, "everything that needs ordering, by supplier"),
    "deliveries": ("Deliveries", "stock:deliveries", ANYONE, "the list of orders on their way and when they are due"),
    "item": ("an item's page", "stock:item_detail", ANYONE, "one item: on hand, usage and history"),
    "stock": ("Stock", "stock:items", ADMIN, "the stock list: add, edit and archive items, prices and order sizes"),
    "suppliers": ("Suppliers", "stock:suppliers", ADMIN, "suppliers' phone, email and delivery times"),
    "spending": ("Spending", "stock:spending", ADMIN, "what's been spent, by supplier and item, for a period"),
    "tax_year": ("Tax year", "stock:tax_year", ADMIN, "stock spending for an income year, for the accountant"),
    "activity": ("Activity", "stock:activity_log", ADMIN, "who did what, and when"),
    "team": ("Team", "team", ADMIN, "staff, roles and codes"),
    "catalogue": ("Catalogue", "stock:catalogue", ADMIN, "add common dental stock from the catalogue"),
    "stocktake": ("Stocktake", "stock:stocktake_step", ADMIN, "count every item in turn"),
}
FILES = {  # key -> (label, URL name); all carry prices or spending, so managers only
    "items": ("items", "stock:export_items"),
    "stock_events": ("stock events", "stock:export_stock_events"),
    "order_lines": ("order lines", "stock:export_order_lines"),
    "tax_year": ("tax year", "stock:export_tax_year"),
}


def _page_declaration(user):
    offered = {key: page for key, page in PAGES.items() if user.role in page[2]}
    properties = {
        "page": {
            "type": "STRING",
            "enum": list(offered),
            "description": "Which page. " + "; ".join(f"{key}: {page[3]}" for key, page in offered.items()),
        },
        "item": {"type": "STRING", "description": "For page 'item': the item's name as written in the practice data."},
    }
    if user.is_org_admin:
        properties["period"] = {"type": "STRING", "enum": list(PERIOD_WEEKS), "description": "For page 'spending'."}
        properties["year"] = {"type": "INTEGER", "description": (
            "For page 'tax_year': the income year, named by the year it ends in (2026 is April 2025 to March 2026).")}
    return {
        "description": (
            "Put a button under your answer that opens a StockRoom page, e.g. for 'where do I change a supplier's "
            "phone number?' or 'show me spending by supplier'. Nothing is changed. Still answer in words when "
            "the data lets you."),
        "parameters": {"type": "OBJECT", "properties": properties, "required": ["page"]},
    }


def _open_page(user, args, pending):
    key = args.get("page")
    if key not in PAGES:
        return "Sorry, I can't open that page from the chat."
    label, url_name, roles, _ = PAGES[key]
    if user.role not in roles:
        return MONEY_REFUSAL  # the same reply as any other money question; the page itself would 403 anyway
    if key == "item":
        item = _item_for(user, args)
        if isinstance(item, Which):  # several fit: a button for each, since opening a page changes nothing
            return [_button(f"Open {o['name']}", reverse(url_name, args=[o["id"]])) for o in item.options]
        if isinstance(item, str):
            return item
        return [_button(f"Open {item.name}", reverse(url_name, args=[item.pk]))]
    url = reverse(url_name)
    if key == "spending" and args.get("period") in PERIOD_WEEKS:
        url += f"?period={args['period']}"
        label += f", this {args['period']}"
    elif key == "tax_year" and (year := _whole(args.get("year"), 2000)):
        url += f"?year={year}"
        label += f" {year}"
    return [_button(f"Open {label}", url)]


def _download_declaration(user):
    return {
        "description": "Put a button under your answer that downloads one of the CSV exports. Nothing is changed.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "file": {"type": "STRING", "enum": list(FILES), "description": (
                    "items: the stock list with prices; stock_events: every use, count and delivery; "
                    "order_lines: every order with prices; tax_year: an income year's invoices.")},
                "year": {"type": "INTEGER", "description": "For file 'tax_year': the income year, named by the year it ends in."},
            },
            "required": ["file"],
        },
    }


def _download(user, args, pending):
    if args.get("file") not in FILES:
        return "Sorry, I can't download that from the chat."
    label, url_name = FILES[args["file"]]
    url = reverse(url_name)
    if args["file"] == "tax_year" and (year := _whole(args.get("year"), 2000)):
        url += f"?year={year}"
        label += f" {year}"
    return [_button(f"Download {label} (CSV)", url, download=True)]


ACTIONS = {action.name: action for action in [
    _event_action(
        "log_used", Kind.USED,
        "Log that some of an item has been used, e.g. 'we used 2 boxes of gloves'. Call it once for each item.",
        qty="How many were used, counted in the item's unit.",
    ),
    _event_action(
        "log_out", Kind.OUT,
        "Log that the last of an item has been used and there's none left, e.g. 'we've run out of 2.2 needles' "
        "or 'used the last of the bibs'. Call it once for each item.",
    ),
    _event_action(
        "log_low", Kind.LOW,
        "Log that an item is running low but isn't out yet, e.g. 'bibs are getting low'. Call it once for each item.",
    ),
    _event_action(
        "set_count", Kind.COUNT,
        "Set the exact number of an item on the shelf, e.g. 'there are 14 boxes of gloves on the shelf'. "
        "Not for what's been used. Call it once for each item.",
        qty="How many are on the shelf now, counted in the item's unit. 0 if there are none.",
    ),
    Action(
        "receive_order", ANYONE,
        {"description": ("Receive everything on order from a supplier that has arrived, e.g. 'the Henry Schein "
                         "order came'."),
         "parameters": {"type": "OBJECT", "properties": {"supplier": {
             "type": "STRING", "description": "The supplier's name as written in the practice data."}},
             "required": ["supplier"]}},
        _receive_order, _apply_receipt, undo="stock:delivery_undo_batch",
    ),
    Action("receive_line", ANYONE, _receive_line_declaration, _receive_line, _apply_receipt,
           undo="stock:delivery_undo_batch"),
    Action("add_to_reorder", ANYONE, _add_declaration, _add_line, _apply_add, undo="stock:reorder_add_undo_batch"),
    _manager_action(
        "remove_from_reorder",
        "Take an item off the reorder list, e.g. 'take gloves off the reorder list' or 'we don't need the gauze "
        "ordered'. Not for something that has been ordered: that's mark_ordered. Call it once for each item.",
        _remove_line, _apply_remove, {"item": ITEM_PROPERTY}, required=["item"], restore=_restore_remove),
    _manager_action(
        "mark_ordered",
        "Mark an item as ordered because it has been ordered from the supplier, e.g. 'I ordered the gloves' or "
        "'ordered 10 boxes of bibs'. Only give qty if they said how many, for this order only. Call it once for "
        "each item. Not for adding to the list.",
        _ordered_line, _apply_order,
        {"item": ITEM_PROPERTY, "qty": {"type": "INTEGER", "description": "How many were ordered this time."}},
        required=["item"], undo="stock:reorder_undo_batch"),
    _manager_action(
        "mark_supplier_ordered",
        "Mark everything on the reorder list from one supplier as ordered, e.g. 'mark the Henry Schein order as "
        "sent' or 'we've ordered everything from Dentsply'.",
        _supplier_order, _apply_order, {"supplier": _supplier_property("Who it was ordered from.")},
        required=["supplier"], undo="stock:reorder_undo_batch"),
    _manager_action(
        "set_order_size",
        "Change how many of an item are ordered at a time from now on, e.g. 'we order bibs in 10s' or 'make the "
        "gloves 10 boxes'. Not for an order already placed: that's mark_ordered with qty.",
        _size_line, _apply_size,
        {"item": ITEM_PROPERTY, "qty": {"type": "INTEGER", "description": "How many to order at a time."}},
        required=["item", "qty"], restore=_restore_size),
    _manager_action(
        "switch_supplier",
        "Order an item from one of its backup suppliers from now on, e.g. 'get the bibs from Southern Cross'. The "
        "old supplier stays as a backup.",
        _switch_line, _apply_switch,
        {"item": ITEM_PROPERTY, "supplier": _supplier_property("Who it should come from.")},
        required=["item", "supplier"], restore=_restore_switch),
    _manager_action(
        "write_order_email",
        "Show the order email for a supplier, with a button to open it in the mail app and one to call them, e.g. "
        "'write the email to Dentsply'. Nothing is sent.",
        _order_email, None, {"supplier": _supplier_property("Who to write to.")}, required=["supplier"], link=True),
    _manager_action(
        "add_item",
        "Add a new item to the stock list, e.g. 'add 3M Filtek Z350 in A2 from Henry Schein, $85 a syringe'. "
        "Leave out anything they didn't say. Not for ordering more of an item they already have.",
        _new_item, _apply_new_item,
        {"name": {"type": "STRING", "description": "The product's name, with its size or variant."},
         "unit": {"type": "STRING", "description": "What one is counted in, singular, e.g. box, syringe, pack."},
         "supplier": _supplier_property("Who it's ordered from, if they said."),
         "price": {"type": "NUMBER", "description": "The price of one unit in dollars, if they said."},
         "order_size": {"type": "INTEGER", "description": "How many are ordered at a time, if they said."}},
        required=["name"], restore=_restore_new),
    _manager_action(
        "edit_item",
        "Change an item's name, unit or preferred supplier, e.g. 'rename the gloves to Nitrile gloves, size M' or "
        "'we get the bibs from Dentsply now'. For a backup supplier use add_backup_supplier. For a price use set_price.",
        _edit_item, _apply_edit,
        {"item": ITEM_PROPERTY, "new_name": {"type": "STRING", "description": "What it should be called."},
         "new_unit": {"type": "STRING", "description": "What it's counted in now, singular."},
         "new_supplier": _supplier_property("Who it's ordered from now.")},
        required=["item"], restore=_restore_edit),
    _manager_action(
        "set_price",
        "Set what one unit of an item costs, e.g. 'gloves are $7.20 a box now'.",
        _price_line, _apply_price,
        {"item": ITEM_PROPERTY, "price": {"type": "NUMBER", "description": "The price of one unit, in dollars."}},
        required=["item", "price"], restore=_restore_price),
    _manager_action(
        "archive_item",
        "Archive an item the practice doesn't use any more, e.g. 'archive the old impression trays'.",
        _archive_line, _apply_archive, {"item": ITEM_PROPERTY}, required=["item"], restore=_restore_archive),
    _manager_action(
        "unarchive_item",
        "Bring back an archived item, e.g. 'bring back the impression trays'.",
        _unarchive_line, _apply_unarchive, {"item": ITEM_PROPERTY}, required=["item"], restore=_restore_unarchive),
    _manager_action(
        "add_backup_supplier",
        "Add a supplier an item can also be ordered from, e.g. 'add Southern Cross as a backup for bibs'.",
        lambda user, args, pending: _backup_line(user, args, pending, add=True), _apply_backup_add,
        {"item": ITEM_PROPERTY, "supplier": _supplier_property("The backup.")}, required=["item", "supplier"],
        restore=_restore_backup_add),
    _manager_action(
        "remove_backup_supplier",
        "Stop listing a supplier as a backup for an item, e.g. 'Southern Cross doesn't sell bibs any more'.",
        lambda user, args, pending: _backup_line(user, args, pending, add=False), _apply_backup_remove,
        {"item": ITEM_PROPERTY, "supplier": _supplier_property("The backup to remove.")},
        required=["item", "supplier"], restore=_restore_backup_remove),
    _manager_action(
        "add_supplier",
        "Add a new supplier, e.g. 'add a supplier called Dental Direct, phone 09 555 0101'.",
        _new_supplier, _apply_new_supplier,
        {"name": {"type": "STRING", "description": "The supplier's name."},
         "phone": {"type": "STRING", "description": "Their phone number, if they said."},
         "email": {"type": "STRING", "description": "Their email address, if they said."},
         "lead_days": {"type": "INTEGER", "description": "Days from ordering to delivery, if they said."}},
        required=["name"], restore=_restore_new_supplier),
    _manager_action(
        "edit_supplier",
        "Change a supplier's details or delivery time, e.g. 'Henry Schein takes 3 days now' or 'Dentsply's new "
        "number is 0800 111 222'.",
        _edit_supplier, _apply_edit_supplier,
        {"supplier": _supplier_property("Which supplier."),
         "new_name": {"type": "STRING", "description": "A new name for them."},
         "phone": {"type": "STRING", "description": "Their phone number now."},
         "email": {"type": "STRING", "description": "Their email address now."},
         "lead_days": {"type": "INTEGER", "description": "Days from ordering to delivery now."}},
        required=["supplier"], restore=_restore_edit_supplier),
    _manager_action(
        "archive_supplier",
        "Archive a supplier the practice doesn't use any more.",
        _archive_supplier_line, _apply_archive_supplier, {"supplier": _supplier_property("Which supplier.")},
        required=["supplier"], restore=_restore_archive_supplier),
    _manager_action(
        "unarchive_supplier",
        "Bring back an archived supplier.",
        _unarchive_supplier_line, _apply_unarchive_supplier, {"supplier": _supplier_property("Which supplier.")},
        required=["supplier"], restore=_restore_unarchive_supplier),
    Action("open_page", ANYONE, _page_declaration, _open_page, None, link=True),
    Action("download", ADMIN, _download_declaration, _download, None, link=True),
]}
