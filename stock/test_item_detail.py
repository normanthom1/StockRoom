from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organisation, User
from stockroom.demo import DEMO_ORG

from .humanize import build_caveat, build_sentence, format_rate_sentence, order_by_text
from .models import Item, StockEvent, Supplier
from .test_forecast import NOW, TODAY, run, weekly_counts


class BuildSentenceTests(TestCase):
    """Unit tests against forecast() output directly - see test_forecast.py's
    NOW/TODAY/run/weekly_counts fixtures."""

    def make_item(self, unit="box"):
        org = Organisation.objects.create(name="Test Dental")
        supplier = Supplier.objects.create(organisation=org, name="Henry Schein", lead_days=5)
        return Item.objects.create(organisation=org, name="Gloves", unit=unit, supplier=supplier)

    def test_high_confidence_sentence_matches_the_issues_style(self):
        # Mirrors the issue's own example: "You use ~12 boxes a week. There
        # are 18 boxes. Henry Schein takes ~5 days. Order by today."
        item = self.make_item("box")
        events = weekly_counts([12, 12, 12, 12], start=90 - 42 + 18)  # -> 18 on hand
        f = run(events, lead=5)
        self.assertEqual(f.on_hand, 18)
        self.assertEqual(round(f.weekly_usage), 12)
        sentence = build_sentence(item, f, TODAY)
        self.assertEqual(
            sentence,
            f"You use ~12 boxes a week. There are 18 boxes. Henry Schein takes ~5 days. {order_by_text(f.order_by, TODAY)}.",
        )

    def test_out_of_stock_sentence(self):
        item = self.make_item("tip")
        f = run(weekly_counts([10, 10], start=20) + [StockEvent(kind="count", qty=0, created_at=NOW)], lead=5)
        self.assertEqual(build_sentence(item, f, TODAY), "There's none left. Henry Schein takes ~5 days, so order today.")

    def test_low_confidence_sentence_gives_a_range_not_a_precise_number(self):
        item = self.make_item("syringe")
        events = weekly_counts([5], start=20)  # 1 week of history only
        f = run(events, lead=5)
        self.assertEqual(f.confidence, "low")
        sentence = build_sentence(item, f, TODAY)
        self.assertIn("Not enough history yet to be precise", sentence)
        self.assertIn("Henry Schein takes ~5 days", sentence)

    def test_low_confidence_caveat(self):
        f = run(weekly_counts([5], start=20), lead=5)
        self.assertIn("Confidence improves", build_caveat(f))

    def test_spike_week_caveat_mentions_the_excluded_week(self):
        f = run(weekly_counts([70, 70, 210, 70, 70, 70, 70, 70], start=600), lead=7)
        self.assertEqual(f.excluded_weeks, 1)
        self.assertIn("left out of the average", build_caveat(f))

    def test_high_confidence_no_spike_has_no_caveat(self):
        f = run(weekly_counts([12, 12, 12, 12], start=90), lead=5)
        self.assertEqual(build_caveat(f), "")

    def test_format_rate_sentence_reads_naturally_inline(self):
        self.assertEqual(format_rate_sentence(12, "box"), "~12 boxes a week")
        self.assertEqual(format_rate_sentence(0.5, "bottle"), "~0.5 bottles a week")
        self.assertEqual(format_rate_sentence(0, "box"), "not enough history to say")


class ItemDetailViewTests(TestCase):
    """Exercises the real seeded shape: gloves (high confidence), composite
    (low confidence), prophy paste cups (spike week)."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")
        org = Organisation.objects.get(name=DEMO_ORG)
        cls.practice = User.objects.get(organisation=org, is_practice_login=True)
        cls.admin = User.objects.get(organisation=org, name="Sandy")
        cls.assistant = User.objects.get(organisation=org, name="Liz")
        cls.gloves = Item.objects.get(organisation=org, name="Nitrile gloves, size M")
        cls.composite = Item.objects.get(organisation=org, name="Composite, A2 syringes")
        cls.prophy = Item.objects.get(organisation=org, name="Prophy paste cups")

    def sign_in(self, staff):
        self.client.force_login(self.practice)
        self.client.post("/accounts/code/", {"pin": staff.pin})

    def test_high_confidence_item_shows_a_precise_sentence(self):
        self.sign_in(self.admin)
        content = self.client.get(f"/item/{self.gloves.pk}/").content.decode()
        self.assertIn("You use ~", content)
        self.assertIn("Henry Schein takes ~5 days", content)
        self.assertIn("Confident", content)

    def test_low_confidence_item_shows_low_confidence_and_a_range(self):
        self.sign_in(self.admin)
        content = self.client.get(f"/item/{self.composite.pk}/").content.decode()
        self.assertIn("Low confidence", content)
        self.assertIn("Not enough history yet to be precise", content)
        self.assertIn("Confidence improves", content)

    def test_spike_week_item_hatches_the_excluded_week_in_the_chart(self):
        self.sign_in(self.admin)
        content = self.client.get(f"/item/{self.prophy.pk}/").content.decode()
        self.assertIn("excluded-week-hatch", content)
        self.assertIn("week excluded from the average", content)
        self.assertIn("left out of the average", content)

    def test_admin_can_set_price(self):
        self.sign_in(self.admin)
        response = self.client.post(f"/item/{self.gloves.pk}/price/", {"price": "9.75"})
        self.assertRedirects(response, f"/item/{self.gloves.pk}/")
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "9.75")

    def test_admin_can_clear_the_price(self):
        self.sign_in(self.admin)
        self.client.post(f"/item/{self.gloves.pk}/price/", {"price": ""})
        self.gloves.refresh_from_db()
        self.assertIsNone(self.gloves.price)

    def test_invalid_price_is_rejected(self):
        self.sign_in(self.admin)
        self.client.post(f"/item/{self.gloves.pk}/price/", {"price": "not-a-number"})
        self.gloves.refresh_from_db()
        self.assertNotEqual(str(self.gloves.price), "not-a-number")

    def test_admin_can_set_order_size(self):
        self.sign_in(self.admin)
        self.client.post(f"/item/{self.gloves.pk}/order-size/", {"order_size": "24"})
        self.gloves.refresh_from_db()
        self.assertEqual(self.gloves.order_size, 24)

    def test_admin_can_toggle_the_reorder_pin(self):
        self.sign_in(self.admin)
        self.assertIsNone(self.composite.pinned_to_reorder_at)
        self.client.post(f"/item/{self.composite.pk}/toggle-reorder/")
        self.composite.refresh_from_db()
        self.assertIsNotNone(self.composite.pinned_to_reorder_at)
        self.client.post(f"/item/{self.composite.pk}/toggle-reorder/")
        self.composite.refresh_from_db()
        self.assertIsNone(self.composite.pinned_to_reorder_at)

    def test_assistant_can_tell_the_manager_were_low(self):
        self.sign_in(self.assistant)
        response = self.client.post(f"/log-usage/{self.gloves.pk}/running-low/")
        self.assertEqual(response["HX-Redirect"], "/")
        self.assertTrue(StockEvent.objects.filter(item=self.gloves, kind="low").exists())

    def test_assistant_item_detail_has_no_admin_price_controls(self):
        self.sign_in(self.assistant)
        content = self.client.get(f"/item/{self.gloves.pk}/").content.decode()
        self.assertNotIn("Save", content)
        self.assertIn("Tell the manager we're low", content)

    def test_assistant_item_detail_offers_setting_the_exact_count(self):
        self.sign_in(self.assistant)
        content = self.client.get(f"/item/{self.gloves.pk}/").content.decode()
        self.assertIn("Set exact count", content)
        self.assertIn(f'hx-get="/item/{self.gloves.pk}/count-sheet/"', content)
