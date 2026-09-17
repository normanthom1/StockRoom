"""Plain-English formatting for stock screens - StockRoom.html ported to Python.

Kept separate from forecast.py: that module works out the numbers, this one
turns them into the words shown on screen.
"""

import re


def phone_digits(phone: str) -> str:
    """A phone number stripped down for a tel: link - keeps a leading + only."""
    return re.sub(r"[^0-9+]", "", phone or "")

# StockRoom.html's UNIT_SINGULAR/pluralUnit, for units already plural by default.
UNIT_SINGULAR = {
    "boxes": "box",
    "cartridges": "cartridge",
    "cups": "cup",
    "rolls": "roll",
    "syringes": "syringe",
    "pouches": "pouch",
    "packs": "pack",
    "tubes": "tube",
}


def pluralize_unit(unit: str, qty: int) -> str:
    if qty == 1:
        return unit
    if unit.endswith(("ch", "sh", "x")):
        return unit + "es"
    return unit + "s"


def format_qty(qty: int | None, unit: str) -> str:
    if qty is None:
        return "not counted yet"
    return f"{qty} {pluralize_unit(unit, qty)}"


def _rate_numbers(weekly_usage: float) -> tuple[float, int]:
    """(display value, whole number for pluralization).

    A whole-number round can hit 0 for a slow-moving item (e.g. 0.5/week),
    which reads as "not used at all" - fall back to one decimal place.
    """
    rounded = round(weekly_usage)
    return (rounded if rounded >= 1 else round(weekly_usage, 1)), rounded


def format_rate(weekly_usage: float, unit: str) -> str:
    if weekly_usage <= 0:
        return "still learning"
    display, rounded = _rate_numbers(weekly_usage)
    return f"~{display} {pluralize_unit(unit, rounded)}/week"


def format_rate_sentence(weekly_usage: float, unit: str) -> str:
    """Like format_rate, but "a week" reads better inline in a sentence."""
    if weekly_usage <= 0:
        return "not enough history to say"
    display, rounded = _rate_numbers(weekly_usage)
    return f"~{display} {pluralize_unit(unit, rounded)} a week"


def humanize_run_out(days: float | None) -> str:
    """Caps the estimate at "6 months+" and rounds to a friendly unit, so a
    precise "~214 days" for something this far off isn't false precision."""
    if days is None:
        return "not tracked yet"
    if days <= 0:
        return "Out now"
    if days > 182:
        return "6 months+"
    if days < 14:
        return f"~{round(days)} days"
    if days < 60:
        return f"~{round(days / 7)} wks"
    return f"~{round(days / 30)} months"


def humanize_range(lo_days: float, hi_days: float) -> str:
    """Like humanize_run_out, but for a low-confidence item's run-out range."""
    if lo_days < 14:
        return f"~{round(lo_days)}–{round(hi_days)} days"
    if lo_days < 60:
        return f"~{round(lo_days / 7)}–{round(hi_days / 7)} wks"
    return f"~{round(lo_days / 30)}–{round(hi_days / 30)} months"


def _day_bucket(date, today):
    """(days from today, weekday abbreviation, "day month") - shared by any
    copy that names a near date without false precision."""
    delta = (date - today).days
    return delta, date.strftime("%a"), f"{date.day} {date.strftime('%b')}"


def order_by_text(order_by, today):
    """"Overdue since Mon" / "Order by today" / "Order by Fri" / "Order by 22 Aug"."""
    if order_by is None:
        return ""
    delta, weekday, day_month = _day_bucket(order_by, today)
    if delta < 0:
        return f"Overdue since {weekday}"
    if delta == 0:
        return "Order by today"
    if delta <= 6:
        return f"Order by {weekday}"
    return f"Order by {day_month}"


def arriving_text(expected, today):
    """"Overdue, arriving ~Thu" / "Arriving today" / "Arriving ~Thu" / "Arriving ~22 Aug"."""
    if expected is None:
        return ""
    delta, weekday, day_month = _day_bucket(expected, today)
    if delta < 0:
        return f"Overdue, arriving ~{weekday}"
    if delta == 0:
        return "Arriving today"
    if delta <= 6:
        return f"Arriving ~{weekday}"
    return f"Arriving ~{day_month}"


CONFIDENCE_LABEL = {"low": "Low confidence", "high": "Confident"}


def part_delivered_text(remainder_line):
    """For an open back-order split off a partial delivery: "3 of 5 arrived,
    2 still coming". remainder_line.split_from is the closed original line."""
    original = remainder_line.split_from
    return f"{original.received_qty} of {original.qty} arrived, {remainder_line.qty} still coming"


def build_sentence(item, f, today):
    """The item detail page's plain-language forecast summary, e.g.
    "You use ~12 boxes a week. There are 18 boxes. Henry Schein takes
    ~5 days. Order by today."
    """
    lead_text = f"{item.supplier.name} takes ~{item.supplier.lead_days} days"

    if f.on_hand == 0:
        return f"There's none left. {lead_text}, so order today."

    if f.confidence == "low":
        run_out_text = humanize_range(*(_days_from(d, today) for d in f.run_out_range)) if f.run_out_range else "soon"
        when = order_by_text(f.order_by, today) if f.order_by else "soon"
        when = when[0].lower() + when[1:]  # mid-sentence, but keep e.g. "Mon" capitalised
        return f"Not enough history yet to be precise. Best guess: you run out in {run_out_text}. {lead_text}, so {when}."

    when = f"{order_by_text(f.order_by, today)}." if f.order_by else ""
    return f"You use {format_rate_sentence(f.weekly_usage, item.unit)}. There are {format_qty(f.on_hand, item.unit)}. {lead_text}. {when}"


def build_caveat(f):
    """Explains why the sentence's number might be off, or "" when it's not needed."""
    if f.confidence == "low":
        return "Confidence improves after a few more weeks of tapping, or count the shelf now and it firms up straight away."
    if f.excluded_weeks:
        return "One week used much more than usual. That week is left out of the average. Two busy weeks in a row and it becomes the new normal instead."
    return ""


def _days_from(when, today):
    return (when - today).days
