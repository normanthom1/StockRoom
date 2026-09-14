"""Seed (or reset) the demo practice (stockroom.demo.DEMO_ORG) with
realistic-looking history, one practice login and four staff codes.

Deterministic: always the same output for the same code, via random.Random(42).
Safe to inspect before running for real practices - it only ever touches the
one organisation named DEMO_ORG.
"""

import random
from datetime import timedelta
from decimal import Decimal

import djclick as click
from django.db import transaction
from django.utils import timezone

from accounts.models import Organisation, User
from stock.forecast import round_order_qty
from stock.models import Item, OrderLine, StockEvent, Supplier
from stockroom.demo import DEMO_EMAIL, DEMO_ORG, DEMO_PASSWORD

WEEK = timedelta(weeks=1)

SUPPLIERS = {
    "Henry Schein": {"lead_days": 5, "phone": "0800 807 707", "email": "orders@henryschein.co.nz"},
    "Dentsply": {"lead_days": 7, "phone": "0800 335 626", "email": "orders@dentsply.co.nz"},
    # A backup for a few items (BACKUPS), to show switching supplier. No phone or
    # email, so the demo's Call and Email buttons can't reach a real business.
    "Independent Dental Supplies": {"lead_days": 3},
}

# Items the practice can also get from another supplier: item name -> backup supplier.
BACKUPS = {
    "Nitrile gloves, size M": "Independent Dental Supplies",
    "Suction tips, disposable": "Independent Dental Supplies",
    "Face masks, Level 2": "Independent Dental Supplies",
    "Gauze squares, 5x5cm": "Independent Dental Supplies",
    "Lignocaine 2% w/ adrenaline": "Henry Schein",
}

# name, code, role - the practice login's staff. Managers have 4-digit codes.
STAFF = [
    ("Sofia", "0000", User.Role.ADMIN),
    ("Johanna", "11", User.Role.ASSISTANT),
    ("Emilio", "22", User.Role.ASSISTANT),
    ("Practice Owner", "5555", User.Role.ADMIN),
]

# name, unit, supplier, weekly usage rate, unit price, shape.
# Shape drives the final on-hand target so the seeded home screen matches the
# prototype: 1 out, 2 order now, a few order this week, the rest fine.
# Rates and prices come from StockRoom.html's ITEMS/FINE_ITEMS/prices objects,
# except FINE_ITEMS prices, which the prototype never assigns - these are
# plausible placeholders for demo purposes only.
ITEMS = [
    ("Suction tips, disposable", "tip", "Henry Schein", 40, "0.20", "out"),
    ("Nitrile gloves, size M", "box", "Henry Schein", 12, "8.50", "order_now"),
    ("Lignocaine 2% w/ adrenaline", "cartridge", "Dentsply", 20, "0.95", "order_now"),
    ("Composite, A2 syringes", "syringe", "Henry Schein", 4.5, "32.00", "short_history"),
    ("Prophy paste cups", "cup", "Dentsply", 70, "0.35", "spike"),
    ("Autoclave pouches 90x230", "pouch", "Henry Schein", 55, "0.12", "ok"),
    ("Barrier film rolls", "roll", "Dentsply", 1.5, "18.00", "ok"),
    ("Face masks, Level 2", "box", "Henry Schein", 2, "12.50", "this_week"),
    ("Face shields", "shield", "Henry Schein", 3, "3.00", "this_week"),
    ("Safety glasses, patient", "pair", "Henry Schein", 5, "4.50", "ok"),
    ("Bib clips", "clip", "Henry Schein", 1, "0.50", "ok"),
    ("Dappen dishes", "dish", "Dentsply", 4, "0.30", "ok"),
    ("Cotton pellets", "pack", "Henry Schein", 1, "2.00", "ok"),
    ("Gauze squares, 5x5cm", "pack", "Henry Schein", 6, "3.50", "ok"),
    ("Articulating paper", "book", "Dentsply", 1, "4.00", "ok"),
    ("Dental floss spools", "spool", "Henry Schein", 2, "1.50", "ok"),
    ("Disclosing tablets", "pack", "Henry Schein", 0.5, "6.00", "ok"),
    ("Fissure sealant", "kit", "Dentsply", 1, "45.00", "ok"),
    ("Etchant gel, 37%", "syringe", "Henry Schein", 1, "8.00", "ok"),
    ("Universal bonding agent", "bottle", "Dentsply", 0.5, "55.00", "ok"),
    ("Glass ionomer cement", "kit", "Henry Schein", 0.5, "60.00", "ok"),
    ("Temporary filling material", "tube", "Dentsply", 1, "12.00", "ok"),
    ("Endodontic files, assorted", "file", "Henry Schein", 4, "3.50", "ok"),
    ("Gutta percha points", "box", "Dentsply", 0.5, "15.00", "ok"),
    ("Paper points, assorted", "box", "Dentsply", 1, "6.00", "ok"),
    ("Irrigation syringes", "syringe", "Henry Schein", 10, "0.80", "ok"),
    ("Sodium hypochlorite solution", "bottle", "Dentsply", 1, "9.00", "ok"),
    ("EDTA gel", "syringe", "Dentsply", 0.5, "14.00", "ok"),
    ("Rubber dam sheets", "sheet", "Henry Schein", 5, "1.20", "ok"),
    ("Rubber dam clamps", "clamp", "Henry Schein", 1, "5.00", "ok"),
    ("Disposable impression trays", "tray", "Henry Schein", 6, "0.90", "ok"),
    ("Alginate powder", "bag", "Dentsply", 2, "18.00", "ok"),
    ("Bite registration paste", "cartridge", "Dentsply", 1, "9.50", "ok"),
    ("Anaesthetic needles, 30G", "needle", "Henry Schein", 25, "0.35", "ok"),
    ("Topical anaesthetic gel", "tub", "Henry Schein", 0.5, "7.00", "ok"),
    ("Surgical sutures", "suture", "Dentsply", 2, "4.00", "ok"),
    ("Scalpel blades, #15", "blade", "Henry Schein", 4, "0.60", "ok"),
    ("Haemostatic gauze", "pack", "Dentsply", 1, "22.00", "ok"),
    ("Digital sensor barriers", "barrier", "Henry Schein", 40, "0.15", "ok"),
    ("Ultrasonic scaler tips", "tip", "Henry Schein", 1, "35.00", "ok"),
    ("Polishing paste", "tub", "Dentsply", 1, "6.50", "ok"),
    ("Take-home whitening gel", "syringe", "Dentsply", 1, "16.00", "ok"),
    ("Disposable mouth mirrors", "mirror", "Henry Schein", 15, "0.40", "ok"),
    ("Sharps containers", "container", "Henry Schein", 0.5, "8.00", "ok"),
]

# Days-left targets per shape, as an offset added to the item's own lead_days
# (stock.forecast.status_of's cutoffs are lead+3 for order now, lead+10 for
# order this week), chosen with margin so noise can't tip a seeded item into
# the wrong bucket.
DAYS_OFFSET = {"order_now": 1, "this_week": 6, "ok": 25, "spike": 25, "short_history": 6}

# An assistant's request waiting on the manager's reorder list: (item, who asked).
ASKED_FOR = ("Gauze squares, 5x5cm", "Johanna")

RATE_BY_NAME = {name: rate for name, _unit, _supplier, rate, _price, _shape in ITEMS}

# Orders still on the way, so the Deliveries page has something to receive
# (one of them late) and this week's and this month's spend aren't zero:
# (item, days since ordered, days until expected).
INCOMING = [
    ("Barrier film rolls", 3, 3),
    ("Rubber dam sheets", 1, 4),
    ("Autoclave pouches 90x230", 5, -1),  # expected in the past, so it shows as "Late"
]


@click.command()
@click.option("--reset", is_flag=True, help="Delete and recreate the demo organisation.")
def command(reset):
    """Create (or --reset) the demo practice with 16 weeks of history."""
    existing = Organisation.objects.filter(name=DEMO_ORG).first()
    if existing:
        if not reset:
            raise click.ClickException(f'"{DEMO_ORG}" already exists. Re-run with --reset to recreate it.')
        with transaction.atomic():
            # Deleted in dependency order: StockEvent/OrderLine protect their
            # user, Item protects its supplier, and Organisation is protected
            # by its users - a plain cascade from the org can't resolve that.
            StockEvent.objects.filter(organisation=existing).delete()
            OrderLine.objects.filter(organisation=existing).delete()
            Item.objects.filter(organisation=existing).delete()
            Supplier.objects.filter(organisation=existing).delete()
            User.objects.filter(organisation=existing).delete()
            existing.delete()

    with transaction.atomic():
        org = Organisation.objects.create(name=DEMO_ORG)
        User.objects.create_user(
            DEMO_EMAIL, DEMO_PASSWORD, organisation=org, name="Reception", role=User.Role.ADMIN, is_practice_login=True
        )
        staff = [User.objects.create_staff(org, name, pin, role) for name, pin, role in STAFF]
        admins = [u for u in staff if u.is_org_admin]
        assistants = [u for u in staff if not u.is_org_admin]

        suppliers = {
            name: Supplier.objects.create(organisation=org, name=name, **info) for name, info in SUPPLIERS.items()
        }

        rng = random.Random(42)
        now = timezone.localtime()
        for name, unit, supplier_name, rate, price, shape in ITEMS:
            item = Item.objects.create(
                organisation=org,
                name=name,
                unit=unit,
                supplier=suppliers[supplier_name],
                price=Decimal(price),
            )
            _seed_item_history(rng, org, item, rate, shape, admins, assistants, now)
            if name in BACKUPS:
                item.other_suppliers.add(suppliers[BACKUPS[name]])

        item_name, asker = ASKED_FOR
        Item.objects.filter(organisation=org, name=item_name).update(
            reorder_requested_by=next(u for u in staff if u.name == asker)
        )

        # Capped by how far into the week/month "now" is, so these always land
        # in the current week's and month's spend, whatever day the nightly
        # reset happens to land on.
        max_days_ago = now.weekday()
        for item_name, days_ago, days_until in INCOMING:
            item = Item.objects.get(organisation=org, name=item_name)
            OrderLine.objects.create(
                organisation=org,
                item=item,
                qty=round_order_qty(RATE_BY_NAME[item_name] * 2),
                unit_price=item.price,
                ordered_by=rng.choice(admins),
                ordered_at=now - timedelta(days=min(days_ago, max_days_ago)),
                expected_at=now + timedelta(days=days_until),
            )

    codes = ", ".join(f"{name} {pin}" for name, pin, _ in STAFF)
    click.echo(f'Seeded "{org.name}" with {len(ITEMS)} items. Practice login: {DEMO_EMAIL} / {DEMO_PASSWORD}. Codes: {codes}')


def _seed_item_history(rng, org, item, weekly_rate, shape, admins, assistants, now):
    weeks = 2 if shape == "short_history" else 16
    lead_days = item.supplier.lead_days
    daily_rate = weekly_rate / 7

    end_on_hand = 0 if shape == "out" else max(1, round((lead_days + DAYS_OFFSET[shape]) * daily_rate))
    final_week_usage = 0 if shape == "out" else max(1, round(weekly_rate * rng.uniform(0.85, 1.15)))

    dates = [now - (weeks - i) * WEEK for i in range(weeks)]
    qtys = [0] * weeks
    qtys[-1] = end_on_hand + final_week_usage
    receipts = []  # (interval index, qty)
    for i in range(weeks - 2, -1, -1):
        consumption = round(weekly_rate * rng.uniform(0.85, 1.15))
        if shape == "spike" and i == weeks - 6:
            consumption = round(weekly_rate * 3)
        receipt = 0
        if item.name != "Suction tips, disposable" and i % 3 == 1:
            receipt = round_order_qty(weekly_rate * 2)
            receipts.append((i, receipt))
        qtys[i] = max(1, qtys[i + 1] + consumption - receipt)

    for i, when in enumerate(dates):
        StockEvent.objects.create(
            organisation=org, item=item, user=rng.choice(admins), kind="count", qty=qtys[i], created_at=when
        )
        if i < weeks - 1:
            for _ in range(rng.randint(1, 3)):
                tap_when = when + timedelta(days=rng.uniform(0.5, 6))
                tap_qty = max(1, round(daily_rate * rng.uniform(0.5, 1.5)))
                StockEvent.objects.create(
                    organisation=org, item=item, user=rng.choice(assistants), kind="used", qty=tap_qty,
                    created_at=tap_when,
                )

    for i, qty in receipts:
        received_at = dates[i] + (dates[i + 1] - dates[i]) / 2
        admin = rng.choice(admins)
        order = OrderLine.objects.create(
            organisation=org,
            item=item,
            qty=qty,
            unit_price=item.price,
            ordered_by=admin,
            ordered_at=received_at - timedelta(days=lead_days),
            received_qty=qty,
            received_at=received_at,
            received_by=admin,
        )
        StockEvent.objects.create(
            organisation=org, item=item, user=admin, kind="received", qty=qty, created_at=order.received_at
        )

    if shape != "out" and final_week_usage:
        remaining = final_week_usage
        taps = min(3, remaining)
        for t in range(taps):
            qty = remaining // (taps - t)
            remaining -= qty
            tap_when = dates[-1] + timedelta(days=rng.uniform(0.5, 6.5))
            StockEvent.objects.create(
                organisation=org, item=item, user=rng.choice(assistants), kind="used", qty=qty, created_at=tap_when
            )
