"""Plain-English formatting for stock screens - StockRoom.html ported to Python.

Kept separate from forecast.py: that module works out the numbers, this one
turns them into the words shown on screen.
"""

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


def format_rate(weekly_usage: float, unit: str) -> str:
    if weekly_usage <= 0:
        return "still learning"
    # A whole-number round can hit 0 for a slow-moving item (e.g. 0.5/week),
    # which reads as "not used at all" - fall back to one decimal place.
    rounded = round(weekly_usage)
    display = rounded if rounded >= 1 else round(weekly_usage, 1)
    return f"~{display} {pluralize_unit(unit, rounded)}/week"


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


def order_by_text(order_by, today):
    """"Overdue since Mon" / "Order by today" / "Order by Fri" / "Order by 22 Aug"."""
    if order_by is None:
        return ""
    delta = (order_by - today).days
    if delta < 0:
        return f"Overdue since {order_by.strftime('%a')}"
    if delta == 0:
        return "Order by today"
    if delta <= 6:
        return f"Order by {order_by.strftime('%a')}"
    return f"Order by {order_by.day} {order_by.strftime('%b')}"
