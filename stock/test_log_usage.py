from datetime import timedelta

from django.contrib.messages import get_messages
from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .models import Item, StockEvent, Supplier


class LogUsageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.other_assistant = User.objects.create_user("johanna@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)

        cls.counted_item = Item.objects.create(
            organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier
        )
        StockEvent.objects.create(
            organisation=cls.org, item=cls.counted_item, user=cls.assistant, kind="count", qty=10
        )
        cls.uncounted_item = Item.objects.create(
            organisation=cls.org, name="New syringes", unit="syringe", supplier=cls.supplier
        )

    def setUp(self):
        self.client.force_login(self.assistant)

    def test_tile_grid_lists_items_most_used_first(self):
        busy = Item.objects.create(organisation=self.org, name="Busy item", unit="box", supplier=self.supplier)
        StockEvent.objects.create(organisation=self.org, item=busy, user=self.assistant, kind="used", qty=50)
        content = self.client.get("/log-usage/").content.decode()
        self.assertLess(content.index("Busy item"), content.index("Gloves"))

    def test_search_filters_the_tile_grid(self):
        response = self.client.get("/log-usage/?q=glov")
        self.assertContains(response, "Gloves")
        self.assertNotContains(response, "New syringes")

    def test_sheet_shows_used_one_only_when_on_hand_is_known(self):
        content = self.client.get(f"/log-usage/{self.counted_item.pk}/sheet/").content.decode()
        self.assertIn("Used 1 box", content)
        self.assertIn("10 boxes", content)

        content = self.client.get(f"/log-usage/{self.uncounted_item.pk}/sheet/").content.decode()
        self.assertNotIn("Used 1", content)
        self.assertIn("Used the last one", content)
        self.assertIn("Running low", content)

    def test_used_one_logs_an_event_and_redirects_home_with_undo(self):
        response = self.client.post(f"/log-usage/{self.counted_item.pk}/used-one/")
        self.assertEqual(response["HX-Redirect"], "/")
        event = StockEvent.objects.get(item=self.counted_item, kind="used")
        self.assertEqual(event.qty, 1)
        self.assertEqual(event.user, self.assistant)

        stored_messages = list(get_messages(response.wsgi_request))
        self.assertIn("Logged: used 1 box of Gloves", str(stored_messages[0]))
        self.assertEqual(stored_messages[0].extra_tags, f"/log-usage/undo/{event.pk}/")

    def test_running_low_logs_a_low_event(self):
        self.client.post(f"/log-usage/{self.counted_item.pk}/running-low/")
        event = StockEvent.objects.get(item=self.counted_item, kind="low")
        self.assertIsNone(event.qty)

    def test_used_last_logs_an_out_event(self):
        self.client.post(f"/log-usage/{self.counted_item.pk}/used-last/")
        event = StockEvent.objects.get(item=self.counted_item, kind="out")
        self.assertIsNone(event.qty)

    def test_action_buttons_have_double_submit_protection(self):
        content = self.client.get(f"/log-usage/{self.counted_item.pk}/sheet/").content.decode()
        self.assertIn('hx-disabled-elt="this"', content)


class LogUndoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.other_assistant = User.objects.create_user("johanna@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def event(self, user, **kwargs):
        return StockEvent.objects.create(organisation=self.org, item=self.item, user=user, kind="low", **kwargs)

    def test_undo_deletes_the_users_own_recent_event(self):
        event = self.event(self.assistant)
        self.client.force_login(self.assistant)
        response = self.client.post(f"/log-usage/undo/{event.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(StockEvent.objects.filter(pk=event.pk).exists())

    def test_undo_refuses_someone_elses_event(self):
        event = self.event(self.other_assistant)
        self.client.force_login(self.assistant)
        response = self.client.post(f"/log-usage/undo/{event.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(StockEvent.objects.filter(pk=event.pk).exists())

    def test_undo_refuses_an_event_older_than_ten_minutes(self):
        event = self.event(self.assistant, created_at=timezone.now() - timedelta(minutes=11))
        self.client.force_login(self.assistant)
        response = self.client.post(f"/log-usage/undo/{event.pk}/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(StockEvent.objects.filter(pk=event.pk).exists())

    def test_undo_allows_an_event_just_under_ten_minutes_old(self):
        event = self.event(self.assistant, created_at=timezone.now() - timedelta(minutes=9))
        self.client.force_login(self.assistant)
        response = self.client.post(f"/log-usage/undo/{event.pk}/")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(StockEvent.objects.filter(pk=event.pk).exists())
