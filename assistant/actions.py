"""What Ask StockRoom can change: one entry in ACTIONS each, and every change
the chat makes goes through here.

The AI proposes and the server decides. Gemini is sent the declarations of
only the actions the person's role allows (what it's never given, it can't
call) and calls one instead of answering. preview() turns that call into a
card showing what will change, or a plain answer when it can't be done, and
nothing is written until the person taps Confirm. apply() then checks the
role again and makes the change through the same code as the button on the
page, returning the toast's text and the page's own undo URL.
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
from stock.matching import Matcher
from stock.models import Item, StockEvent
from stock.views import SOLID_STATUSES, STATUS_COLOR, _forecast_for, log_event

ANYONE = frozenset(User.Role.values)
MAX_QTY = 9999  # anything bigger is a misheard number, not a day's use


@dataclass(frozen=True)
class Action:
    name: str
    roles: frozenset  # the User.Role values that may use it
    declaration: dict  # what Gemini is told about it: description and parameters
    preview: Callable  # (user, args) -> the card's proposal dict, or a plain answer when it can't be done
    apply: Callable  # (user, proposal) -> (toast text, undo URL)


def declarations(user):
    """The function declarations this person's role allows, for gemini.generate(tools=...)."""
    return [{"name": action.name, **action.declaration} for action in ACTIONS.values() if user.role in action.roles]


def propose(user, calls):
    """The card for the change Gemini asked for, or a plain answer when it can't be made."""
    name, args = calls[0]
    action = ACTIONS.get(name)
    if action is None or user.role not in action.roles:
        return "Sorry, I can't do that from the chat."
    proposal = action.preview(user, args if isinstance(args, dict) else {})
    if isinstance(proposal, str):
        return proposal
    if len(calls) > 1:
        # ponytail: one change per card until a card can apply and undo several (#152).
        proposal["note"] = "One change at a time for now. Ask again for the rest."
    return {"action": name, **proposal}


def apply(user, proposal):
    """Make a confirmed change in one transaction. None if this person may no
    longer make it (their role changed) or its items have gone."""
    action = ACTIONS.get(proposal["action"])
    if action is None or user.role not in action.roles:
        return None
    try:
        with transaction.atomic():
            return action.apply(user, proposal)
    except ObjectDoesNotExist:
        return None


def _find(user, name):
    """The practice's active item by this name. Only a sure match: a close
    spelling would be a guess, and the chat never guesses."""
    match = Matcher(user.organisation).match(name)
    return match.item if match.band == "sure" else None


def _chip(f):
    return {"status_label": f.status.label, "status_color": STATUS_COLOR[f.status], "solid": f.status in SOLID_STATUSES}


def _whole(value):
    """A count from Gemini (JSON numbers can arrive as 2.0), or None if it isn't a sensible one."""
    if isinstance(value, int | float) and 1 <= value <= MAX_QTY and value == int(value):
        return int(value)
    return None


# --- Used N of an item: Log usage's "Used 1", for any number ---

def _preview_used(user, args):
    name = str(args.get("item") or "").strip()
    item = _find(user, name) if name else None
    if item is None:
        return f"I can't find '{name}' in your stock." if name else "Which item was it?"
    qty = _whole(args.get("qty"))
    if qty is None:
        return f"How many {pluralize_unit(item.unit, 2)} of {item.name} were used?"
    now = timezone.localtime()
    before = _forecast_for(item, now)
    if before.on_hand is None:
        # Log usage doesn't offer "Used 1" here either: there's no count to take it from.
        return (f"{item.name} hasn't been counted yet, so there's nothing to take {qty} off. "
                "Count it first: tap it on Log usage, then Set exact count.")
    after = _forecast_for(item, now, extra=[StockEvent(kind=StockEvent.Kind.USED, qty=qty, created_at=now)])
    used = format_qty(qty, item.unit)
    return {
        "lines": [{
            "item": item.pk, "qty": qty, "name": item.name, "change": f"Used {used}",
            "before": before.on_hand, "after": after.on_hand, "unit": pluralize_unit(item.unit, after.on_hand),
            "was": _chip(before), "now": _chip(after),
        }],
        "summary": f"used {used} of {item.name}",
    }


def _apply_used(user, proposal):
    [line] = proposal["lines"]
    item = Item.objects.for_org(user.organisation).get(pk=line["item"], is_active=True)
    # The card's id as the event's client_id: a second Confirm racing the first can't log it twice.
    event, _ = log_event(user, item, StockEvent.Kind.USED, line["qty"], client_id=uuid.UUID(proposal["id"]))
    return (f"Logged: used {format_qty(line['qty'], item.unit)} of {item.name} · the manager will see it",
            reverse("stock:log_undo", args=[event.pk]))


ACTIONS = {action.name: action for action in [
    Action(
        name="log_used",
        roles=ANYONE,
        declaration={
            "description": "Log that some of an item has been used, e.g. 'we used 2 boxes of gloves'.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "item": {"type": "STRING", "description": "The item's name as written in the practice data."},
                    "qty": {"type": "INTEGER", "description": "How many were used, counted in the item's unit."},
                },
                "required": ["item", "qty"],
            },
        },
        preview=_preview_used,
        apply=_apply_used,
    ),
]}
