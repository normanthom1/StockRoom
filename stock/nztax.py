"""New Zealand tax rules StockRoom has to get right: income years, how long a
record has to be kept, and whether a GST number is real.

Pure functions over dates and strings, no database, so they can be checked
against a hand calculation and against IRD's own published examples.

What the law asks for, and where each rule comes from:

- **Income year.** The standard balance date is 31 March, so the income year
  runs 1 April to 31 March (Income Tax Act 2007, s YE 1). A practice with a
  non-standard balance date approved by the Commissioner has different dates,
  which StockRoom does not model; its reports say so rather than guessing.

- **Seven years.** Business records must be kept for at least seven years
  after the end of the income year they relate to (Tax Administration Act
  1994, s 22(2); Goods and Services Tax Act 1985, s 75(3)). The Commissioner
  can require longer, so `retain_until` is the earliest a record may go, never
  a date anything is deleted on automatically.

- **GST number.** A GST number is the registered person's IRD number, so it
  passes IRD's check-digit test. Worth validating because the number is read
  off a photo by an AI model, and a misread digit is silent otherwise.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

# Records must be kept for 7 years after the end of the income year (TAA s 22(2)).
RETENTION_YEARS = 7

# The standard balance date. A non-standard one needs the Commissioner's approval.
BALANCE_MONTH, BALANCE_DAY = 3, 31


def income_year(day: date) -> int:
    """The income year `day` falls in, named by the calendar year it ends in.

    The 2026 income year runs 1 April 2025 to 31 March 2026, which is how IRD
    and every accountant refer to it.
    """
    return day.year if (day.month, day.day) <= (BALANCE_MONTH, BALANCE_DAY) else day.year + 1


def income_year_bounds(year: int) -> tuple[date, date]:
    """[start, end) for an income year, so a filter can use gte/lt and never
    double count 31 March or miss it."""
    return date(year - 1, BALANCE_MONTH + 1, 1), date(year, BALANCE_MONTH + 1, 1)


def income_year_label(year: int) -> str:
    """How IRD writes it: the 2026 income year is "2025-26"."""
    return f"{year - 1}-{str(year)[2:]}"


def income_year_end(year: int) -> date:
    return date(year, BALANCE_MONTH, BALANCE_DAY)


def retain_until(day: date) -> date:
    """The earliest date a record dated `day` may be thrown away: seven years
    after the end of the income year it belongs to (TAA s 22(2))."""
    return income_year_end(income_year(day) + RETENTION_YEARS)


def income_years_back(today: date, count: int) -> list[int]:
    """The current income year and the ones before it, newest first."""
    current = income_year(today)
    return [current - n for n in range(count)]


# --- GST numbers -----------------------------------------------------------

# IRD's check-digit algorithm. A number is 8 or 9 digits; the last is the check
# digit. The primary weights are tried first, then the secondary ones when the
# primary pass gives a check digit of 10.
PRIMARY_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)
SECONDARY_WEIGHTS = (7, 4, 3, 2, 5, 2, 7, 6)
# IRD numbers are issued in this range; outside it the number is not one.
LOWEST, HIGHEST = 10_000_000, 150_000_000


def normalise_gst_number(raw: str) -> str:
    """Digits only, so "49-091-850" and "49 091 850" are the same number."""
    return "".join(character for character in str(raw or "") if character.isdigit())


def is_valid_gst_number(raw: str) -> bool:
    """Whether this is a real IRD (and so GST) number, by IRD's check digit.

    Catches a digit the AI misread off a photo, which would otherwise sit in
    the record looking fine until someone tried to use it.
    """
    digits = normalise_gst_number(raw)
    if not 8 <= len(digits) <= 9 or not LOWEST <= int(digits) <= HIGHEST:
        return False
    body, check = digits[:-1].rjust(8, "0"), int(digits[-1])
    for weights in (PRIMARY_WEIGHTS, SECONDARY_WEIGHTS):
        total = sum(int(digit) * weight for digit, weight in zip(body, weights, strict=True))
        remainder = total % 11
        calculated = 0 if remainder == 0 else 11 - remainder
        if calculated == check:
            return True
        if calculated != 10:  # only a check digit of 10 falls through to the secondary weights
            return False
    return False


def format_gst_number(raw: str) -> str:
    """As IRD prints it: 49-091-850 for 8 digits, 136-410-132 for 9. Anything
    else is handed back as it came, rather than dressed up as a number."""
    digits = normalise_gst_number(raw)
    if len(digits) not in (8, 9):
        return digits
    head = len(digits) - 6
    return f"{digits[:head]}-{digits[head:head + 3]}-{digits[head + 3:]}"


# --- GST amounts -----------------------------------------------------------

# 15% since 1 October 2010, so GST is 3/23 of a GST-inclusive amount.
GST_RATE = Decimal("0.15")
GST_FRACTION_OF_INCLUSIVE = Decimal(3) / Decimal(23)


def gst_from_inclusive(total_incl):
    """The GST in a GST-inclusive amount: 3/23 of it at a 15% rate.

    Only used to fill a gap where an invoice shows a total but no GST line,
    and the report says when a figure came from this rather than off the
    invoice. The rate has been 15% since 1 October 2010; an invoice older than
    that would need the earlier rate, which StockRoom doesn't model.
    """
    if total_incl is None:
        return None
    return (Decimal(total_incl) * GST_FRACTION_OF_INCLUSIVE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
