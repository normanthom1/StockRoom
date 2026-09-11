"""Stock levels and the reorder forecast for one item.

Pure functions over an item's StockEvents and open OrderLines. They're worked
out fresh on every request with no cache, which is cheap for a practice's
~44 items. Anything with `kind`, `qty` and `created_at` works as an event, and
anything with `qty` works as an order line.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from operator import attrgetter
from statistics import mean, median

from django.db import models
from django.utils import timezone

DAY = timedelta(days=1)
WEEK = timedelta(weeks=1)
WINDOW_WEEKS = 8
OUTLIER_FACTOR = 2.5
HIGH_CONFIDENCE_HISTORY = timedelta(weeks=4)
LOW_FLAG_DAYS = 4
ORDER_BUFFER_DAYS = 3
STOCKTAKES = ("count", "out")


class Status(models.TextChoices):
    OUT = "out", "Out of stock"
    ORDER_NOW = "order_now", "Order now"
    ORDER_THIS_WEEK = "order_this_week", "Order this week"
    OK = "ok", "OK"
    ON_ORDER = "on_order", "On order"


@dataclass(frozen=True)
class Forecast:
    on_hand: int | None  # None until the item has been counted or received
    weekly_usage: float
    excluded_weeks: int  # outlier weeks left out of weekly_usage
    confidence: str  # "low" (under 4 weeks of history) or "high"
    days_left: float | None  # None when nothing says it's being used up
    run_out: date | None
    run_out_range: tuple[date, date] | None  # shown instead of run_out when confidence is low
    order_by: date | None
    status: Status
    incoming: int  # qty on open orders
    order_qty: int


def forecast(events, open_orders, *, lead_days, order_size=None, now=None) -> Forecast:
    """Everything the stock screens show for one item.

    open_orders are the item's OrderLines that are neither received nor
    cancelled. Dates are calendar dates in now's timezone, so pass the
    practice's local time.
    """
    now = now or timezone.localtime()
    events = sorted((e for e in events if e.created_at <= now), key=attrgetter("created_at"))

    stock = on_hand(events)
    weeks, excluded = drop_outliers(weekly_consumption(events, now))
    weekly = mean(weeks) if weeks else 0.0
    history = now - events[0].created_at if events else timedelta(0)
    confidence = "high" if history >= HIGH_CONFIDENCE_HISTORY else "low"
    days = days_left(stock, weekly, events)

    run_out = run_out_range = order_by = None
    if days is not None:
        run_out = (now + days * DAY).date()
        # ponytail: calendar days; switch to business days if suppliers quote those.
        order_by = run_out - (lead_days + ORDER_BUFFER_DAYS) * DAY
        if confidence == "low":
            # ponytail: a fixed band of 0.5x to 1.5x the usage rate; derive it from the
            # spread of weekly usage once there's enough real data to judge.
            run_out_range = ((now + days / 1.5 * DAY).date(), (now + days / 0.5 * DAY).date())

    return Forecast(
        on_hand=stock,
        weekly_usage=weekly,
        excluded_weeks=excluded,
        confidence=confidence,
        days_left=days,
        run_out=run_out,
        run_out_range=run_out_range,
        order_by=order_by,
        status=Status.ON_ORDER if open_orders else status_of(days, lead_days),
        incoming=sum(o.qty for o in open_orders),
        order_qty=order_size or default_order_qty(weekly),
    )


def on_hand(events) -> int | None:
    """On hand = the last count (an `out` is a count of 0) + received since - used since, floored at 0.

    With no count yet, the count is taken as 0. None if the item has never been
    counted or received, because then nothing is known about the shelf.
    """
    base, since = None, events
    for i, e in enumerate(events):
        if e.kind in STOCKTAKES:
            base, since = _stocktake_qty(e), events[i + 1 :]
    if base is None and not any(e.kind == "received" for e in events):
        return None
    received = sum(e.qty for e in since if e.kind == "received")
    used = sum(e.qty for e in since if e.kind == "used")
    return max(0, (base or 0) + received - used)


def weekly_consumption(events, now: datetime, window_weeks: int = WINDOW_WEEKS) -> list[float]:
    """Consumption in each of the last `window_weeks` weeks, newest first, each scaled to 7 days.

    Weeks go back no further than the first event, and the oldest can be a
    partial week. Between two stocktakes, consumption = count1 + received - count2
    (floored at 0), spread evenly over the time between them. Outside the
    first-to-last stocktake span, "used" taps are the consumption. Taps inside it
    are ignored, because people only tap some of the time and the counts are the
    truth. `window_weeks` defaults to the forecast's own averaging window; the
    item detail chart asks for a longer one (12 weeks) purely for display.
    """
    if not events:
        return []
    start = events[0].created_at
    n = min(window_weeks, math.ceil((now - start) / WEEK))
    if n == 0:
        return []

    stocktakes = [e for e in events if e.kind in STOCKTAKES]
    spans = []
    for a, b in pairwise(stocktakes):
        received = sum(e.qty for e in events if e.kind == "received" and a.created_at < e.created_at <= b.created_at)
        if b.created_at > a.created_at:
            spans.append((a.created_at, b.created_at, max(0, _stocktake_qty(a) + received - _stocktake_qty(b))))

    weeks = [0.0] * n
    for e in events:
        if e.kind != "used" or (stocktakes and stocktakes[0].created_at < e.created_at < stocktakes[-1].created_at):
            continue
        weeks[min(n - 1, int((now - e.created_at) / WEEK))] += e.qty

    for i in range(n):
        lo, hi = max(start, now - (i + 1) * WEEK), now - i * WEEK
        for a, b, amount in spans:
            overlap = min(hi, b) - max(lo, a)
            if overlap > timedelta(0):
                weeks[i] += amount * (overlap / (b - a))
        weeks[i] *= WEEK / (hi - lo)
    return weeks


def outlier_mask(weeks: list[float]) -> list[bool]:
    """True for each week left out of the average: more than 2.5x the window
    median, unless a neighbouring week is high too (two or more high weeks in
    a row are the new normal, not an outlier). Nothing is flagged when the
    median is 0 (e.g. sparse taps), since every week would count as high.
    """
    limit = OUTLIER_FACTOR * median(weeks) if weeks else 0
    if limit <= 0:
        return [False] * len(weeks)
    high = [w > limit for w in weeks]
    return [
        high[i] and not (i > 0 and high[i - 1]) and not (i + 1 < len(weeks) and high[i + 1])
        for i in range(len(weeks))
    ]


def drop_outliers(weeks: list[float]) -> tuple[list[float], int]:
    """Leave out weeks flagged by outlier_mask. Returns (kept weeks, number left out)."""
    mask = outlier_mask(weeks)
    kept = [w for w, excluded in zip(weeks, mask) if not excluded]
    return kept, sum(mask)


def days_left(stock: int | None, weekly_usage: float, events) -> float | None:
    """Days left = on hand / daily rate (weekly usage / 7).

    0 when nothing's on hand, and None when on hand is unknown or nothing is
    being used. A `low` flag caps it at 4 days until the next count or receipt.
    """
    if stock == 0:
        days = 0.0
    elif stock is not None and weekly_usage > 0:
        days = stock / (weekly_usage / 7)
    else:
        days = None
    if _flagged_low(events):
        days = LOW_FLAG_DAYS if days is None else min(days, LOW_FLAG_DAYS)
    return days


def status_of(days: float | None, lead_days: int) -> Status:
    """Mirrors the prototype's statusOf. Out at 0 days or less, order now within
    lead + 3 days, order this week within lead + 10 days, otherwise OK. Unknown
    days count as OK."""
    if days is None:
        return Status.OK
    if days <= 0:
        return Status.OUT
    if days <= lead_days + 3:
        return Status.ORDER_NOW
    if days <= lead_days + 10:
        return Status.ORDER_THIS_WEEK
    return Status.OK


def default_order_qty(weekly_usage: float) -> int:
    """About 2 weeks of usage, rounded to a friendly size. 1 when usage is unknown."""
    return round_order_qty(weekly_usage * 2) if weekly_usage > 0 else 1


def round_order_qty(n: float) -> int:
    """The prototype's roundOrderQty: up to tens at 50+, up to fives at 10+, otherwise up to a whole number (at least 1)."""
    if n >= 50:
        return math.ceil(n / 10) * 10
    if n >= 10:
        return math.ceil(n / 5) * 5
    return max(1, math.ceil(n))


def _stocktake_qty(event) -> int:
    return 0 if event.kind == "out" else event.qty


def _flagged_low(events) -> bool:
    resets = [e.kind for e in events if e.kind in ("low", "received", *STOCKTAKES)]
    return bool(resets) and resets[-1] == "low"
