from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .humanize import format_rate, humanize_range, humanize_run_out, order_by_text
from .models import Item, StockEvent, Supplier


class HomeViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.now = timezone.localtime()

        cls.out_item = Item.objects.create(
            organisation=cls.org, name="Suction tips", unit="tip", supplier=cls.supplier
        )
        cls._event(cls.out_item, "count", 0, cls.now - timedelta(weeks=5))

        cls.ok_item = Item.objects.create(
            organisation=cls.org, name="Barrier film", unit="roll", supplier=cls.supplier
        )
        cls._event(cls.ok_item, "count", 100, cls.now - timedelta(weeks=5))
        cls._event(cls.ok_item, "used", 2, cls.now - timedelta(days=2))

    @classmethod
    def _event(cls, item, kind, qty, when):
        return StockEvent.objects.create(
            organisation=cls.org, item=item, user=cls.admin, kind=kind, qty=qty, created_at=when
        )

    def test_out_item_is_listed_before_the_fine_item(self):
        self.client.force_login(self.admin)
        content = self.client.get("/").content.decode()
        self.assertIn("Suction tips", content)
        self.assertIn("Out of stock", content)
        self.assertLess(content.index("Suction tips"), content.index("Barrier film"))

    def test_fine_item_shows_a_humanized_run_out_estimate(self):
        self.client.force_login(self.admin)
        content = self.client.get("/").content.decode()
        self.assertIn("6 months+", content)  # ~100 rolls at ~1/week

    def test_admin_sees_the_reorder_count(self):
        self.client.force_login(self.admin)
        content = self.client.get("/").content.decode()
        self.assertIn("Reorder list &middot; 1", content)

    def test_assistant_sees_the_waiting_message_not_the_count_link(self):
        self.client.force_login(self.assistant)
        content = self.client.get("/").content.decode()
        self.assertIn("1 waiting for the manager to order", content)
        self.assertNotIn("Reorder list &middot;", content)

    def test_tapping_a_row_opens_the_item_detail(self):
        self.client.force_login(self.assistant)
        response = self.client.get(f"/item/{self.out_item.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Suction tips")

    def test_item_detail_404s_for_another_organisations_item(self):
        other_org = Organisation.objects.create(name="Other Dental")
        other_user = User.objects.create_user("them@example.com", "pw", organisation=other_org)
        self.client.force_login(other_user)
        response = self.client.get(f"/item/{self.out_item.pk}/")
        self.assertEqual(response.status_code, 404)


class HumanizeTests(TestCase):
    def test_humanize_run_out(self):
        cases = [(0, "Out now"), (200, "6 months+"), (10, "~10 days"), (21, "~3 wks"), (90, "~3 months")]
        for days, expected in cases:
            with self.subTest(days):
                self.assertEqual(humanize_run_out(days), expected)
        self.assertEqual(humanize_run_out(None), "not tracked yet")

    def test_format_rate_does_not_round_a_slow_item_to_zero(self):
        # round(0.5) is 0 in Python (banker's rounding) - would misleadingly
        # read as "not used at all".
        self.assertEqual(format_rate(0.5, "bottle"), "~0.5 bottles/week")
        self.assertEqual(format_rate(12.0, "box"), "~12 boxes/week")
        self.assertEqual(format_rate(0, "box"), "still learning")

    def test_humanize_range(self):
        self.assertEqual(humanize_range(3, 9), "~3–9 days")
        self.assertEqual(humanize_range(20, 40), "~3–6 wks")

    def test_order_by_text(self):
        today = timezone.localtime().date()
        self.assertEqual(order_by_text(today - timedelta(days=2), today), f"Overdue since {(today - timedelta(days=2)).strftime('%a')}")
        self.assertEqual(order_by_text(today, today), "Order by today")
        self.assertEqual(order_by_text(today + timedelta(days=3), today), f"Order by {(today + timedelta(days=3)).strftime('%a')}")
        far = today + timedelta(days=20)
        self.assertEqual(order_by_text(far, today), f"Order by {far.day} {far.strftime('%b')}")
        self.assertEqual(order_by_text(None, today), "")
