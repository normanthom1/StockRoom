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
from stock.humanize import format_qty, pluralize_unit
from stock.matching import Matcher, synonym_key
from stock.models import Invoice, Item, StockEvent, Supplier
from stock.spending import PERIOD_WEEKS
from stock.views import (
    SOLID_STATUSES,
    STATUS_COLOR,
    WANTED_STATUSES,
    _forecast_for,
    _open_lines_for_org,
    fill_invoice,
    log_event,
    pending_invoice,
    receive_delivery_line,
)

ANYONE = frozenset(User.Role.values)
ADMIN = frozenset({User.Role.ADMIN})
MONEY_REFUSAL = "Sorry, your account needs manager access to answer questions about prices or spending."
MAX_QTY = 9999  # anything bigger is a misheard number, not a day's use
MIN_UNUSUAL = 200  # a count has to pass this, and 10x anything seen before, to be asked about
MAX_OPTIONS = 6  # buttons on a "Which one?"
Kind = StockEvent.Kind


@dataclass(frozen=True)
class Action:
    name: str
    roles: frozenset  # the User.Role values that may use it
    declaration: dict | Callable  # what Gemini is told about it: description and parameters (or user -> that)
    preview: Callable  # (user, args, pending) -> a card line, a Which, or a plain answer when it can't be done
    apply: Callable | None  # (user, line, client_id) -> the id of what it made
    undo: str = "stock:log_undo_batch"  # URL name, given ?ids= of what apply made; also the family of a card
    # A link action changes nothing, so it has no card or Confirm: preview returns buttons, or a plain answer.
    link: bool = False


@dataclass(frozen=True)
class Which:
    """A name that fits several things (items, suppliers, orders): asked, never guessed."""

    name: str
    options: list  # [{"id": pk, "name": ...}]
    kind: str = "item"  # what the ids are, so a pick can't be taken for another kind's


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
    for i in sorted(range(len(calls)), key=lambda n: calls[n][0] == "receive_order"):
        name, args = calls[i]
        action = ACTIONS.get(name)
        if action is None or user.role not in action.roles:
            problems.append("Sorry, I can't do that from the chat.")
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
            which = which or {"key": f"{i}.{result.kind}", "name": result.name, "options": result.options}
        else:
            lines += [{"action": name, **line} for line in (result if isinstance(result, list) else [result])]
    if not lines and not which and not buttons and not problems:
        return "Sorry, I can't do that from the chat."
    if not lines and not which:
        buttons = list({b["url"]: b for b in buttons}.values())
        if buttons:
            # Why something couldn't be done beats the model's own words, which may say it was.
            return Answer(" ".join(problems) or text or "Here you go.", buttons)
        return problems[0]
    summaries = [line["summary"] for line in lines] or [f"asked which '{which['name']}' you meant"]
    return {
        "calls": calls, "picks": picks, "lines": lines, "which": which, "note": " ".join(problems),
        "summary": ", ".join(summaries[:-1]) + (" and " if len(summaries) > 1 else "") + summaries[-1],
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
    undo = reverse(todo[0][0].undo) + "?ids=" + ",".join(str(i) for i in ids)
    text = lines[0]["toast"] if len(lines) == 1 else lines[0]["many"].format(n=len(lines))
    return text, undo


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


# --- Buttons under an answer: open a page or download a file. Nothing changes, so there's no card. ---

def _button(label, url, download=False):
    return {"label": label, "url": url, "download": download}


# What the model may open, by name: key -> (label, URL name, roles, what's there). It never supplies a URL.
# The views behind them keep their own role checks; the roles here only decide who's offered each.
PAGES = {
    "home": ("Home", "stock:home", ANYONE, "what to order today"),
    "log_usage": ("Log usage", "stock:log_usage", ANYONE, "log what's been used, running low or counted"),
    "reorder_list": ("Reorder list", "stock:reorder_list", ANYONE, "everything that needs ordering, by supplier"),
    "deliveries": ("Deliveries", "stock:deliveries", ANYONE, "receive what's on order"),
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
    Action("open_page", ANYONE, _page_declaration, _open_page, None, link=True),
    Action("download", ADMIN, _download_declaration, _download, None, link=True),
]}
