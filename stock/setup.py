"""Setting a new practice up from its paperwork.

A practice that's just signed up has no items, no suppliers and no prices, so
its first invoice upload can't receive anything: every line lands unmatched on
an invoice that needs checking. This module reads those lines back the other
way, as a draft of what the practice actually orders.

Nothing here parses anything. The existing batch import (stock/invoices.py)
has already read each file into Invoice and InvoiceLine rows, including the
ones it couldn't match, so drafting is a plain read over lines with no item.

Nothing is created until a manager confirms the draft, which is the same rule
the invoice preview follows.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from statistics import median

from django.conf import settings
from django.db import transaction

from accounts.models import User

from .matching import normalize
from .models import Invoice, InvoiceLine, Item, StockEvent, Supplier

# Units a dental invoice line usually counts in, longest first so "box of 100"
# doesn't match before "box" is ruled out. Mirrors the catalogue's own units.
UNIT_WORDS = (
    "cartridge", "canister", "syringe", "sachet", "bottle", "carton", "needle",
    "shield", "packet", "blade", "pouch", "strip", "tube", "pack", "roll",
    "case", "gown", "pair", "box", "bag", "tub", "cup", "tip", "kit", "cap",
)
UNIT_PATTERN = re.compile(r"\b(" + "|".join(UNIT_WORDS) + r")e?s?\b", re.IGNORECASE)
DEFAULT_UNIT = "box"


def guess_unit(text):
    """What one of it is counted in, read off the product description.
    "Nitrile gloves 100/box" is a box; anything unreadable is a box too, which
    is the commonest by far and is a dropdown on the review screen anyway."""
    found = UNIT_PATTERN.search(text or "")
    return found.group(1).lower() if found else DEFAULT_UNIT


@dataclass
class Draft:
    """One product the practice buys, gathered from every invoice line for it.

    The editable fields (name, unit, supplier_name, price, order_size, on_hand)
    start as what the invoices suggest and are overwritten by whatever the
    manager typed on the review screen before create_drafts saves them.
    """

    key: str  # normalise(description), which is how the lines were grouped
    name: str
    unit: str
    supplier_name: str
    sku: str
    price: Decimal | None
    order_size: int | None
    on_hand: int | None
    times: int  # invoice lines behind it, i.e. how often they buy it
    total_qty: int
    last_seen: date | None
    line_ids: list[int] = field(default_factory=list)

    @property
    def is_regular(self):
        """Bought more than once, so it's stock rather than a one-off purchase."""
        return self.times > 1


def _seen_on(line):
    return line.invoice.issued_on or line.invoice.created_at.date()


def draft_items(org):
    """A draft item per distinct product on the practice's invoices, commonest
    first. Lines already matched to an item are left out, and so is anything
    whose name is one of the practice's items under another spelling, so
    confirming a draft twice can't make the same item twice."""
    lines = (
        InvoiceLine.objects.for_org(org)
        .filter(item__isnull=True)
        .exclude(invoice__status=Invoice.Status.IGNORED)  # a repeat would double the counts
        .select_related("invoice", "invoice__supplier")
        .order_by("pk")
    )
    stocked = {normalize(name) for name in Item.objects.for_org(org).values_list("name", flat=True)}

    groups = {}
    for line in lines:
        text = line.description or line.sku
        key = normalize(text)
        if not key or key in stocked:
            continue
        groups.setdefault(key, []).append(line)

    return sorted((_draft(key, group) for key, group in groups.items()),
                  key=lambda d: (-d.times, -(d.last_seen or date.min).toordinal(), d.name.lower()))


def _draft(key, lines):
    """One product's draft, from its invoice lines oldest to newest.

    Price and supplier come from the newest line, because that's what they pay
    now. Order size is the typical invoiced quantity, which is how much they
    buy at a time. On hand starts as the newest quantity: what last arrived is
    the best guess at what's on the shelf, and the manager corrects it.
    """
    lines = sorted(lines, key=_seen_on)
    newest = lines[-1]
    quantities = [line.qty for line in lines if line.qty]
    priced = [line for line in lines if line.unit_price is not None]
    named = [line.description for line in lines if line.description]
    supplier = [line.invoice.supplier.name if line.invoice.supplier else line.invoice.supplier_name
                for line in lines]

    return Draft(
        key=key,
        # The fullest spelling of the name, which reads better than a truncated one.
        name=max(named, key=len) if named else newest.sku,
        unit=guess_unit(newest.description),
        supplier_name=next((name for name in reversed(supplier) if name), ""),
        sku=next((line.sku for line in reversed(lines) if line.sku), ""),
        price=priced[-1].unit_price if priced else None,
        order_size=round(median(quantities)) if quantities else None,
        on_hand=newest.qty,
        times=len(lines),
        total_qty=sum(quantities),
        last_seen=_seen_on(newest),
        line_ids=[line.pk for line in lines],
    )


@transaction.atomic
def create_drafts(org, user, drafts):
    """Turn confirmed drafts into the practice's suppliers, items and first
    counts. Suppliers named on the invoices are created here rather than during
    import, because by now a manager has read the name and agreed to it.

    Each draft's invoice lines are pointed at the item it became, so they leave
    the draft, and so the item's spending history goes back to its first
    invoice. They're deliberately not received: the count below is what's on the
    shelf today, and receiving a year of old deliveries on top would double it.
    """
    suppliers = {s.name.lower(): s for s in Supplier.objects.for_org(org)}
    created = []
    for draft in drafts:
        supplier = suppliers.get(draft.supplier_name.lower())
        if supplier is None:
            supplier = Supplier.objects.create(organisation=org, name=draft.supplier_name)
            suppliers[supplier.name.lower()] = supplier
        elif not supplier.is_active:
            supplier.is_active = True
            supplier.save(update_fields=["is_active"])

        item = Item.objects.create(
            organisation=org,
            name=draft.name,
            unit=draft.unit,
            supplier=supplier,
            price=draft.price,
            order_size=draft.order_size,
            # So next month's invoice matches by code instead of by name.
            supplier_sku=draft.sku[:64],
        )
        InvoiceLine.objects.for_org(org).filter(pk__in=draft.line_ids, item__isnull=True).update(item=item)
        if draft.on_hand is not None:
            StockEvent.objects.create(organisation=org, item=item, user=user, kind="count", qty=draft.on_hand)
        created.append(item)
    return created


def reads_invoices():
    """Whether this install can read an invoice at all. Every invoice view is
    @ai_required, so without a key they 404: the checklist must not send a
    manager to one."""
    return bool(settings.AI_API_KEY)


def steps(org, drafts_waiting):
    """The setup checklist: what's done, what's next, and where each one goes.

    Deliberately not stored. Every step is answered by the data itself, so a
    practice that sets up some other way (the catalogue, a CSV, by hand) sees
    the checklist tick itself off rather than nagging about a path it didn't take.

    Without an AI key there is no invoice reading, so the first step becomes the
    catalogue and the draft step drops out: every step left is one the practice
    can actually do.
    """
    has_stock = Item.objects.for_org(org).filter(is_active=True).exists()
    counted = StockEvent.objects.for_org(org).filter(kind="count").exists()
    team = User.objects.for_org(org).filter(is_practice_login=False, is_active=True).count()
    if not reads_invoices():
        first = [{
            "title": "Pick what you stock",
            "blurb": "Choose what you order from the shared catalogue of products NZ practices use, "
                     "or bring in a list you already keep as a CSV. Prices and counts can come later.",
            "done": has_stock,
            "url": "stock:catalogue",
            "cta": "Pick from the catalogue",
        }]
    else:
        first = [
            {
                "title": "Bring in what you order",
                "blurb": "Upload a batch of invoices or receipts and StockRoom drafts your stock list, "
                         "suppliers and prices from them. Or pick from the catalogue if you'd rather start fresh.",
                # Invoices read and waiting count: the next thing to do is check them,
                # not upload more, so this step shouldn't still be the one highlighted.
                "done": has_stock or bool(drafts_waiting),
                "url": "stock:invoice_upload",
                "cta": "Upload invoices",
            },
            {
                "title": "Check the draft",
                "blurb": "Read down what StockRoom found, fix anything it got wrong, and untick what you don't stock. "
                         "Nothing is added until you say so.",
                "done": has_stock and not drafts_waiting,
                "url": "stock:setup_draft",
                "cta": f"Check {drafts_waiting} item{'s' if drafts_waiting != 1 else ''}" if drafts_waiting else "Check the draft",
                "waiting": drafts_waiting,
            },
        ]
    return [
        *first,
        {
            "title": "Count what's on the shelf",
            "blurb": "One pass through the stockroom to set the starting numbers. "
                     "After this the counts keep themselves up to date from what the team taps.",
            "done": counted,
            "url": "stock:stocktake_step",
            "cta": "Start counting",
        },
        {
            "title": "Add the team",
            "blurb": "Everyone gets their own code: 2 digits for an assistant, 4 for a manager. "
                     "They tap it on any device the practice is signed in on.",
            "done": team > 1,
            "url": "team",
            "cta": "Add the team",
        },
    ]
