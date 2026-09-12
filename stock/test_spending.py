from datetime import datetime, time, timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .models import Item, OrderLine, Supplier
from .spending import period_bounds


def _at(d, hour=12):
    return timezone.make_aware(datetime.combine(d, time(hour)))


class SpendingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.henry = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.dentsply = Supplier.objects.create(organisation=cls.org, name="Dentsply", lead_days=7)
        cls.gloves = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.henry)
        cls.masks = Item.objects.create(organisation=cls.org, name="Masks", unit="box", supplier=cls.henry)
        cls.ligno = Item.objects.create(organisation=cls.org, name="Lignocaine", unit="cartridge", supplier=cls.dentsply)

        cls.today = timezone.localtime().date()
        cls.week_start, cls.week_end = period_bounds("week", cls.today)
        mid_week = cls.week_start + timedelta(days=1)

        # Hand calculation: 10 * 8.50 = 85.00, 5 * 12.00 = 60.00, 4 * 20.00 = 80.00
        # -> total 225.00. Henry Schein = 145.00 (64%), Dentsply = 80.00 (36%).
        cls.line_gloves = OrderLine.objects.create(
            organisation=cls.org, item=cls.gloves, qty=10, unit_price="8.50", ordered_by=cls.admin, ordered_at=_at(mid_week)
        )
        cls.line_masks = OrderLine.objects.create(
            organisation=cls.org, item=cls.masks, qty=5, unit_price="12.00", ordered_by=cls.admin, ordered_at=_at(mid_week)
        )
        cls.line_ligno = OrderLine.objects.create(
            organisation=cls.org, item=cls.ligno, qty=4, unit_price="20.00", ordered_by=cls.admin, ordered_at=_at(mid_week)
        )

        # Excluded: cancelled, no price, and outside the week.
        OrderLine.objects.create(
            organisation=cls.org, item=cls.gloves, qty=100, unit_price="8.50", ordered_by=cls.admin,
            ordered_at=_at(mid_week), cancelled_at=timezone.now(),
        )
        OrderLine.objects.create(
            organisation=cls.org, item=cls.gloves, qty=100, unit_price=None, ordered_by=cls.admin, ordered_at=_at(mid_week)
        )
        OrderLine.objects.create(
            organisation=cls.org, item=cls.gloves, qty=100, unit_price="8.50", ordered_by=cls.admin,
            # Next week - outside the current week, and clear of the 3-prior-
            # weeks comparison window too, so it never leaks into either calc.
            ordered_at=_at(cls.week_end),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_actual_total_matches_the_hand_calculation(self):
        content = self.client.get("/spending/").content.decode()
        self.assertIn("225.00", content)

    def test_supplier_breakdown_matches_the_hand_calculation(self):
        content = self.client.get("/spending/").content.decode()
        self.assertIn("145.00", content)
        self.assertIn("64%", content)
        self.assertIn("80.00", content)
        self.assertIn("36%", content)

    def test_top_items_are_listed(self):
        content = self.client.get("/spending/").content.decode()
        self.assertIn("Gloves", content)
        self.assertIn("Masks", content)
        self.assertIn("Lignocaine", content)

    def test_comparison_is_hidden_without_three_prior_periods_of_history(self):
        content = self.client.get("/spending/").content.decode()
        self.assertNotIn("previous 3 periods", content)

    def test_comparison_shown_once_three_prior_periods_exist(self):
        for weeks_ago in (1, 2, 3):
            OrderLine.objects.create(
                organisation=self.org, item=self.gloves, qty=1, unit_price="10.00", ordered_by=self.admin,
                ordered_at=_at(self.week_start - timedelta(weeks=weeks_ago)),
            )
        content = self.client.get("/spending/").content.decode()
        self.assertIn("previous 3 periods", content)
        # Hand calculation: (10 + 10 + 10) / 3 = 10.00 average.
        self.assertIn("10.00", content)

    def test_month_and_year_periods_are_selectable(self):
        for period in ("month", "year"):
            with self.subTest(period):
                response = self.client.get(f"/spending/?period={period}")
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Actual spend")

    def test_invalid_period_falls_back_to_week(self):
        response = self.client.get("/spending/?period=nonsense")
        self.assertContains(response, "225.00")

    def test_estimated_spend_uses_weekly_usage_times_price(self):
        item = Item.objects.create(organisation=self.org, name="Syringes", unit="syringe", supplier=self.henry, price="2.00")
        from .models import StockEvent

        StockEvent.objects.create(organisation=self.org, item=item, user=self.admin, kind="count", qty=100, created_at=timezone.now() - timedelta(weeks=4))
        StockEvent.objects.create(organisation=self.org, item=item, user=self.admin, kind="count", qty=60, created_at=timezone.now())
        # ~10/week * $2.00 = ~$20/week estimated for this one item.
        content = self.client.get("/spending/").content.decode()
        self.assertIn("Estimated ongoing spend", content)

    def test_items_with_no_price_are_counted_as_missing(self):
        Item.objects.create(organisation=self.org, name="Unpriced thing", unit="box", supplier=self.henry)
        content = self.client.get("/spending/").content.decode()
        self.assertIn("with no price set", content)

    def test_assistant_gets_403(self):
        assistant = User.objects.create_user("liz@example.com", "pw", organisation=self.org)
        self.client.force_login(assistant)
        self.assertEqual(self.client.get("/spending/").status_code, 403)
