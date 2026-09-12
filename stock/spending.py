"""Calendar period math for the spending report. Pure date arithmetic, no DB
access, so it's easy to test against a hand calculation.
"""

from datetime import date, timedelta

# Weeks per period, for scaling a weekly usage rate into an estimate.
PERIOD_WEEKS = {"week": 1, "month": 52 / 12, "year": 52}


def period_bounds(period: str, today: date) -> tuple[date, date]:
    """[start, end) for the calendar period containing `today`."""
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=7)
    if period == "month":
        start = today.replace(day=1)
        return start, _add_months(start, 1)
    if period == "year":
        start = today.replace(month=1, day=1)
        return start, start.replace(year=start.year + 1)
    raise ValueError(f"Unknown period: {period}")


def previous_period_bounds(period: str, start: date, n: int) -> list[tuple[date, date]]:
    """The `n` periods immediately before `start`, oldest first."""
    bounds = []
    end = start
    for _ in range(n):
        begin = _step_back(period, end)
        bounds.append((begin, end))
        end = begin
    return list(reversed(bounds))


def _step_back(period, end):
    if period == "week":
        return end - timedelta(days=7)
    if period == "month":
        return _add_months(end, -1)
    return end.replace(year=end.year - 1)


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return d.replace(year=year, month=month, day=1)
