from collections import Counter

import click
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organisation, User
from stockroom.demo import DEMO_ORG

from .forecast import Status, forecast
from .models import Item


def seeded_statuses():
    org = Organisation.objects.get(name=DEMO_ORG)
    counts = Counter()
    for item in Item.objects.filter(organisation=org):
        open_orders = [o for o in item.order_lines.all() if o.is_open]
        f = forecast(list(item.events.all()), open_orders, lead_days=item.supplier.lead_days)
        counts[f.status] += 1
    return org, counts


class SeedDemoTests(TestCase):
    def test_creates_the_demo_shape(self):
        call_command("seed_demo")
        org, counts = seeded_statuses()

        self.assertEqual(Item.objects.filter(organisation=org).count(), 44)
        self.assertEqual(counts[Status.OUT], 1)
        self.assertEqual(counts[Status.ORDER_NOW], 2)
        self.assertGreaterEqual(counts[Status.ORDER_THIS_WEEK], 1)
        practice = User.objects.get(organisation=org, is_practice_login=True)
        self.assertEqual(practice.email, "reception@discoverdental.co.nz")
        self.assertTrue(practice.check_password("password"))
        codes = dict(User.objects.filter(organisation=org, pin__isnull=False).values_list("name", "pin"))
        self.assertEqual(codes, {"Sandy": "00", "Johanna": "11", "Liz": "22", "Practice Owner": "55"})

    def test_is_deterministic(self):
        call_command("seed_demo")
        _, first = seeded_statuses()
        call_command("seed_demo", "--reset")
        _, second = seeded_statuses()
        self.assertEqual(first, second)

    def test_composite_has_low_confidence_from_short_history(self):
        call_command("seed_demo")
        org, _ = seeded_statuses()
        composite = Item.objects.get(organisation=org, name="Composite, A2 syringes")
        f = forecast(list(composite.events.all()), [], lead_days=composite.supplier.lead_days)
        self.assertEqual(f.confidence, "low")

    def test_prophy_spike_week_is_excluded(self):
        call_command("seed_demo")
        org, _ = seeded_statuses()
        prophy = Item.objects.get(organisation=org, name="Prophy paste cups")
        f = forecast(list(prophy.events.all()), [], lead_days=prophy.supplier.lead_days)
        self.assertEqual(f.excluded_weeks, 1)

    def test_refuses_to_run_twice_without_reset(self):
        call_command("seed_demo")
        with self.assertRaises(click.ClickException):
            call_command("seed_demo")

    def test_reset_only_touches_the_demo_organisation(self):
        other = Organisation.objects.create(name="Someone Else's Practice")
        User.objects.create_user("them@example.com", "pw", organisation=other)
        call_command("seed_demo")
        call_command("seed_demo", "--reset")
        self.assertTrue(Organisation.objects.filter(pk=other.pk).exists())
        self.assertEqual(User.objects.filter(organisation=other).count(), 1)
