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

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from stock.humanize import format_qty, pluralize_unit
from stock.matching import Matcher, synonym_key
from stock.models import Item, StockEvent
from stock.spending import PERIOD_WEEKS
from stock.views import (
    SOLID_STATUSES,
    STATUS_COLOR,
    WANTED_STATUSES,
    _forecast_for,
    log_event,
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
    """A name that fits several items: asked, never guessed."""

    name: str
    options: list  # [{"item": pk, "name": ...}]


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
    return build(user, [[name, args if isinstance(args, dict) else {}] for name, args in calls], text=text)


def build(user, calls, picks=None, text=""):
    """The card (a dict kept in the session) for calls, a list of [name, args].
    picks is {str(call index): item pk} for each "Which one?" already answered.
    A call that can't be made is a note on the card while another can; when
    none can, the first reason is the answer, as plain text."""
    picks = picks or {}
    lines, which, family = [], None, None
    pending = {}  # item pk -> the unsaved events earlier lines would make, so later ones start from them
    problems, buttons = [], []
    for i, (name, args) in enumerate(calls):
        action = ACTIONS.get(name)
        if action is None or user.role not in action.roles:
            problems.append("Sorry, I can't do that from the chat.")
            continue
        args = {key: value for key, value in args.items() if key != "item_id"}  # only a pick sets it
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
        if str(i) in picks:
            args["item_id"] = picks[str(i)]
        result = action.preview(user, args, pending)
        if isinstance(result, str):
            problems.append(result)
            continue
        family = action.undo
        if isinstance(result, Which):
            which = which or {"index": i, "name": result.name, "options": result.options}
        else:
            lines.append({"action": name, **result})
    if not lines and not which:
        buttons = list({b["url"]: b for b in buttons}.values())
        if buttons:
            return Answer(text or " ".join(problems) or "Here you go.", buttons)
        return problems[0]
    summaries = [line["summary"] for line in lines] or [f"asked which '{which['name']}' you meant"]
    return {
        "calls": calls, "picks": picks, "lines": lines, "which": which, "note": " ".join(problems),
        "summary": ", ".join(summaries[:-1]) + (" and " if len(summaries) > 1 else "") + summaries[-1],
    }


def apply(user, proposal):
    """Make a confirmed card's changes in one transaction. None if this person
    may no longer make them (their role changed) or an item has gone."""
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
    text = lines[0]["toast"] if len(lines) == 1 else f"Logged {len(lines)} changes · the manager will see it"
    return text, undo


def _item_for(user, args):
    """The item named: an Item, a Which when several fit, or a plain answer saying why not.
    Only a sure match is used as it is; anything less is put to the person."""
    org = user.organisation
    if "item_id" in args:
        item = Item.objects.for_org(org).filter(pk=args["item_id"], is_active=True).first()
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
    return Which(name, [{"item": item.pk, "name": item.name} for item in sorted(fits, key=lambda i: i.name)])


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
        "toast": toast, "before": before.on_hand, "after": after.on_hand,
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
            return [_button(f"Open {o['name']}", reverse(url_name, args=[o["item"]])) for o in item.options]
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
    Action("open_page", ANYONE, _page_declaration, _open_page, None, link=True),
    Action("download", ADMIN, _download_declaration, _download, None, link=True),
]}
