"""Walk-forward backtest of stock.forecast (issue #40): how well it would have
predicted a practice's own past. Nothing replaces the moving average until it
beats these numbers; see docs/forecast-backtest.md.

Every week, going back from the practice's latest event, forecast() runs on
only what was known at that moment, and its weekly usage x 2 is the
prediction for the next 2 weeks. What was actually used is measured in
hindsight from the counts either side of those 2 weeks, the same way the
forecast measures consumption, so a week with no count after its 2 weeks
can't be scored and is skipped.

A stock-out is a count of 0 (or "used the last one") when there was stock
before it. It was missed if, the supplier's lead time before it happened, the
forecast still said OK: too late for an order to arrive in time.
"""

from dataclasses import dataclass, field
from operator import attrgetter
from statistics import mean

from .forecast import (
    DAY,
    STOCKTAKES,
    WEEK,
    Status,
    forecast,
    on_hand,
    weekly_consumption,
)

HORIZON_WEEKS = 2


@dataclass
class ItemBacktest:
    errors: list[float] = field(default_factory=list)  # predicted - actual use, one per scored week
    actuals: list[float] = field(default_factory=list)  # actual use over the 2 weeks, same order
    stockouts: int = 0
    missed: int = 0

    @property
    def mae(self):
        return mean(abs(e) for e in self.errors)

    @property
    def bias(self):
        return mean(self.errors)


def backtest_item(events, orders, *, lead_days, end) -> ItemBacktest:
    """Score one item's forecasts. `end` is the latest moment with data (the
    practice's newest event); cutoffs step back a week at a time from 2 weeks before it."""
    events = sorted(events, key=attrgetter("created_at"))
    stocktakes = [e for e in events if e.kind in STOCKTAKES]
    result = ItemBacktest()
    horizon = HORIZON_WEEKS * WEEK

    cutoff = end - horizon
    while stocktakes and cutoff >= stocktakes[0].created_at:
        after = next((s for s in stocktakes if s.created_at >= cutoff + horizon), None)
        if after:
            predicted = forecast_at(events, orders, cutoff, lead_days).weekly_usage * HORIZON_WEEKS
            known = [e for e in events if e.created_at <= after.created_at]
            actual = sum(weekly_consumption(known, cutoff + horizon, window_weeks=HORIZON_WEEKS))
            result.errors.append(predicted - actual)
            result.actuals.append(actual)
        cutoff -= WEEK

    for i, e in enumerate(events):
        if e.kind not in STOCKTAKES or (e.kind == "count" and e.qty != 0):
            continue
        check = e.created_at - lead_days * DAY
        # Already out (or never counted) before it, or no history yet to forecast from.
        if not on_hand(events[:i]) or check < events[0].created_at:
            continue
        result.stockouts += 1
        if forecast_at(events, orders, check, lead_days).status == Status.OK:
            result.missed += 1
    return result


def forecast_at(events, orders, when, lead_days):
    """forecast() as it stood at `when`. It already ignores later events, but
    not orders placed, received or cancelled later."""
    open_orders = [
        o for o in orders if o.ordered_at <= when and all(t is None or t > when for t in (o.received_at, o.cancelled_at))
    ]
    return forecast(events, open_orders, lead_days=lead_days, now=when)
