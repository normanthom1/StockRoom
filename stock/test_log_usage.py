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

    def test_a_tile_says_its_status_in_words_not_just_its_stripe_colour(self):
        StockEvent.objects.create(organisation=self.org, item=self.counted_item, user=self.assistant, kind="out")
        content = self.client.get("/log-usage/").content.decode()
        self.assertIn('<span class="block font-semibold text-status-out">Out of stock</span>', content)

    def test_tiles_carry_what_the_browser_needs_to_search_offline(self):
        content = self.client.get("/log-usage/").content.decode()
        self.assertIn('data-search="gloves"', content)
        self.assertIn('data-search="new syringes"', content)
        self.assertIn('placeholder="Search all 2 items…"', content)

    def test_tiles_carry_what_the_sheet_shows_so_it_opens_offline(self):
        content = self.client.get("/log-usage/").content.decode()
        # Counted: "Used 1" shows 10 boxes -> 9 boxes. Uncounted: no "Used 1" button.
        self.assertIn('data-qty="10 boxes" data-after="9 boxes" data-has-qty="1"', content)
        self.assertIn('data-has-qty=""', content)
        self.assertIn(f'data-used-one="/log-usage/{self.counted_item.pk}/used-one/"', content)
        self.assertIn(f'data-used-last="/log-usage/{self.counted_item.pk}/used-last/"', content)
        self.assertIn(f'data-running-low="/log-usage/{self.counted_item.pk}/running-low/"', content)
        self.assertIn(f'data-count-sheet="/item/{self.counted_item.pk}/count-sheet/"', content)
        self.assertIn("Used the last one", content)
        self.assertIn("Set exact count", content)
        self.assertIn("Running low", content)

    def test_capture_buttons_send_a_client_id_and_are_marked_for_the_offline_queue(self):
        content = self.client.get("/log-usage/").content.decode()
        # app.js adds the client_id and X-Capture header to anything inside data-capture.
        self.assertIn(f'data-capture="{self.assistant.pk}"', content)

    def test_used_one_logs_an_event_and_stays_on_the_page_with_undo(self):
        response = self.client.post(f"/log-usage/{self.counted_item.pk}/used-one/")
        # Reloads where the tap came from, so the next item is one tap away.
        self.assertEqual(response["HX-Refresh"], "true")
        self.assertNotIn("HX-Redirect", response)
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
        content = self.client.get("/log-usage/").content.decode()
        self.assertEqual(content.count('hx-disabled-elt="this"'), 3)


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
        # The page behind the toast still shows the tap, so it reloads.
        self.assertEqual(response["HX-Refresh"], "true")
        self.assertEqual([str(m) for m in get_messages(response.wsgi_request)], ["Undone."])
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
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockEvent.objects.filter(pk=event.pk).exists())
