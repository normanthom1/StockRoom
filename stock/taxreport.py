"""What the practice spent on stock in an income year, from its invoices.

Separate from `spending.py`, which estimates and compares recent spend for a
manager deciding what to order. This is the other question: what actually went
out, on the dates the invoices say, in the years IRD asks about.

Three deliberate differences from the spending report:

- **Invoices, not orders.** The spending page adds up `OrderLine`s, so it only
  sees what was ordered through StockRoom. An expense figure has to come from
  what the supplier actually billed, including things ordered by phone.

- **Income years, not calendar ones.** 1 April to 31 March (stock/nztax.py).

- **Repeats are excluded and counted.** An invoice ignored as a duplicate
  contributes nothing to the total, and the report says how many there were,
  so the figure can be reconciled against a pile of paper without wondering
  where the difference went.

What this is not: a complete set of business expenses, and not advice on what
is deductible. It covers stock bought through StockRoom and nothing else, and
`DISCLAIMER` says so wherever a total is shown.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from . import nztax
from .models import Invoice

ZERO = Decimal("0.00")

DISCLAIMER = (
    "Stock invoices entered in StockRoom only. It isn't a full set of business expenses, "
    "and it doesn't work out what's deductible. Give it to your accountant alongside "
    "everything else, don't file from it on its own."
)

# Dated by the invoice, so the figures are on an invoice (accruals) basis.
BASIS_NOTE = (
    "Invoices are counted in the income year they were issued in, which suits income tax "
    "and a GST invoice basis. If the practice files GST on a payments basis, the timing "
    "is different, because StockRoom records when an invoice was issued, not when it was paid."
)

# An invoice with no date can't be put in a year, so it's listed to be fixed.
UNDATED_NOTE = "These have no date on them, so they aren't in any year's total yet."


@dataclass
class SupplierTotal:
    name: str
    gst_number: str
    invoices: int
    excl_gst: Decimal = ZERO
    gst: Decimal = ZERO
    incl_gst: Decimal = ZERO


@dataclass
class YearTotal:
    """One income year's stock spend, and how sound the figure is."""

    year: int
    excl_gst: Decimal = ZERO
    gst: Decimal = ZERO
    incl_gst: Decimal = ZERO
    invoices: int = 0
    duplicates_ignored: int = 0
    # Invoices whose GST wasn't printed and had to be worked back out of the
    # total, and ones with no total at all, so the figure can be trusted or not.
    gst_estimated: int = 0
    without_totals: int = 0
    # No money on them at all, so they're in no figure and need a look.
    unusable: int = 0
    missing_gst_number: int = 0
    no_document: int = 0
    by_supplier: list = field(default_factory=list)

    @property
    def label(self):
        return nztax.income_year_label(self.year)

    @property
    def ends_on(self):
        return nztax.income_year_end(self.year)

    @property
    def is_complete(self):
        """Whether every figure came off an invoice rather than being derived."""
        return not (self.gst_estimated or self.without_totals or self.unusable)

    @property
    def needs_attention(self):
        """Counts worth showing the manager before they hand this to anyone."""
        return self.gst_estimated or self.without_totals or self.unusable \
            or self.missing_gst_number or self.no_document


@dataclass
class Amounts:
    """One invoice's money, and how much of it was actually printed on it."""

    excl_gst: Decimal
    gst: Decimal
    incl_gst: Decimal
    gst_printed: bool  # False when GST was worked back out at 15%
    totals_printed: bool  # False when the figure came from the lines instead


def amounts_for(invoice):
    """One invoice's amounts, or None when it shows no money at all.

    Uses the invoice's own totals wherever it prints them, because that is what
    the supplier charged, freight and rounding included. Only falls back to the
    sum of the product lines when there is no total, and says so, because that
    fallback misses exactly the freight the line parser drops.
    """
    excl, gst, incl = invoice.total_excl_gst, invoice.gst_amount, invoice.total_incl_gst
    totals_printed = excl is not None or incl is not None
    gst_printed = gst is not None

    # Fill in whichever of the three is missing from the other two.
    if incl is None and excl is not None and gst is not None:
        incl = excl + gst
    if excl is None and incl is not None and gst is not None:
        excl = incl - gst
    if gst is None and excl is not None and incl is not None:
        gst, gst_printed = incl - excl, True

    if not totals_printed:
        line_total = sum((line.line_total or ZERO for line in invoice.lines.all()), ZERO)
        if not line_total:
            return None
        excl = line_total

    if gst is None:
        if incl is not None:  # a GST-inclusive total with no GST line shown
            gst = nztax.gst_from_inclusive(incl)
            excl = incl - gst
        else:
            gst = (excl * nztax.GST_RATE).quantize(Decimal("0.01"))
            incl = excl + gst
    if incl is None:
        incl = excl + gst
    if excl is None:
        excl = incl - gst
    return Amounts(excl, gst, incl, gst_printed, totals_printed)


def year_total(org, year):
    """One income year's stock spend, by supplier."""
    start, end = nztax.income_year_bounds(year)
    invoices = list(
        Invoice.objects.for_org(org)
        .filter(issued_on__gte=start, issued_on__lt=end)
        .select_related("supplier")
        .prefetch_related("lines", "documents")
        .order_by("issued_on", "pk")
    )
    total = YearTotal(year=year)
    suppliers = {}
    for invoice in invoices:
        if invoice.status == Invoice.Status.IGNORED:
            # A repeat of one already counted. Excluded from every figure, so
            # uploading the same invoice twice can never double the expense.
            total.duplicates_ignored += 1
            continue
        amounts = amounts_for(invoice)
        if amounts is None:
            total.unusable += 1
            continue
        excl, gst, incl = amounts.excl_gst, amounts.gst, amounts.incl_gst
        total.gst_estimated += not amounts.gst_printed
        total.without_totals += not amounts.totals_printed
        total.missing_gst_number += not invoice.supplier_gst_number
        total.no_document += not invoice.documents.all()

        total.invoices += 1
        total.excl_gst += excl
        total.gst += gst
        total.incl_gst += incl

        name = invoice.supplier.name if invoice.supplier else (invoice.supplier_name or "Not named")
        entry = suppliers.get(name)
        if entry is None:
            entry = suppliers[name] = SupplierTotal(
                name=name, gst_number=nztax.format_gst_number(invoice.supplier_gst_number), invoices=0
            )
        if not entry.gst_number and invoice.supplier_gst_number:
            entry.gst_number = nztax.format_gst_number(invoice.supplier_gst_number)
        entry.invoices += 1
        entry.excl_gst += excl
        entry.gst += gst
        entry.incl_gst += incl

    total.by_supplier = sorted(suppliers.values(), key=lambda s: -s.incl_gst)
    return total


def years_with_invoices(org, today):
    """Every income year the practice has an invoice in, newest first, always
    including the current one so a new practice sees a year rather than
    nothing at all."""
    dates = (Invoice.objects.for_org(org).exclude(issued_on=None)
             .order_by("issued_on").values_list("issued_on", flat=True))
    first, last = dates.first(), dates.last()
    current = nztax.income_year(today)
    if first is None:
        return [current]
    newest = max(current, nztax.income_year(last))
    return list(range(newest, nztax.income_year(first) - 1, -1))


def undated(org):
    """Invoices with no date, which therefore aren't in any year's total."""
    return (Invoice.objects.for_org(org).filter(issued_on=None)
            .exclude(status=Invoice.Status.IGNORED).order_by("-created_at"))
