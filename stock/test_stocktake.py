from django.contrib.messages import get_messages
from django.test import TestCase

from accounts.models import Organisation, User

from .models import Item, StockEvent, Supplier


class ItemCountSheetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)
        StockEvent.objects.create(organisation=cls.org, item=cls.item, user=cls.admin, kind="count", qty=10)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_sheet_shows_the_current_count(self):
        content = self.client.get(f"/item/{self.item.pk}/count-sheet/").content.decode()
        self.assertIn('value="10"', content)

    def test_saving_a_count_updates_on_hand_and_redirects_home_with_undo(self):
        response = self.client.post(f"/item/{self.item.pk}/count/", {"qty": "7"})
        self.assertEqual(response["HX-Redirect"], "/")
        event = StockEvent.objects.get(item=self.item, kind="count", qty=7)
        self.assertEqual(event.user, self.admin)
        stored_messages = list(get_messages(response.wsgi_request))
        self.assertIn("Counted Gloves", str(stored_messages[0]))
        self.assertEqual(stored_messages[0].extra_tags, f"/log-usage/undo/{event.pk}/")

    def test_saving_a_count_can_also_set_the_order_size(self):
        self.client.post(f"/item/{self.item.pk}/count/", {"qty": "7", "order_size": "24"})
        self.item.refresh_from_db()
        self.assertEqual(self.item.order_size, 24)

    def test_invalid_count_re_renders_the_sheet_with_an_error(self):
        response = self.client.post(f"/item/{self.item.pk}/count/", {"qty": "not-a-number"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("HX-Redirect", response)
        self.assertContains(response, "Enter a whole number")
        self.assertFalse(StockEvent.objects.filter(item=self.item, kind="count", qty__isnull=True).exists())

    def test_negative_count_is_rejected(self):
        response = self.client.post(f"/item/{self.item.pk}/count/", {"qty": "-3"})
        self.assertContains(response, "Enter a whole number")

    def test_home_quick_count_button_is_admin_only(self):
        self.client.force_login(self.assistant)
        content = self.client.get("/").content.decode()
        self.assertNotIn("count-sheet", content)


class StocktakeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.gloves = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)
        cls.masks = Item.objects.create(organisation=cls.org, name="Masks", unit="box", supplier=cls.supplier)
        cls.archived = Item.objects.create(
            organisation=cls.org, name="Old thing", unit="box", supplier=cls.supplier, is_active=False
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_starting_a_stocktake_shows_the_first_item_and_progress(self):
        response = self.client.get("/stocktake/")
        self.assertContains(response, "Item 1 of 2")
        self.assertContains(response, "Gloves")
        self.assertNotContains(response, "Old thing")  # archived items are skipped

    def test_saving_a_step_advances_to_the_next_item(self):
        self.client.get("/stocktake/")  # starts the session
        response = self.client.post("/stocktake/save/", {"qty": "5"}, follow=True)
        self.assertContains(response, "Item 2 of 2")
        self.assertContains(response, "Masks")
        self.assertTrue(StockEvent.objects.filter(item=self.gloves, kind="count", qty=5).exists())

    def test_a_half_finished_stocktake_can_be_resumed(self):
        self.client.get("/stocktake/")
        self.client.post("/stocktake/save/", {"qty": "5"})
        # A fresh request (e.g. a new session tab) still picks up where it left off.
        response = self.client.get("/stocktake/")
        self.assertContains(response, "Item 2 of 2")
        self.assertContains(response, "Masks")

    def test_finishing_the_last_item_completes_the_stocktake(self):
        self.client.get("/stocktake/")
        self.client.post("/stocktake/save/", {"qty": "5"})
        response = self.client.post("/stocktake/save/", {"qty": "3"}, follow=True)
        self.assertRedirects(response, "/")
        self.assertContains(response, "Stocktake complete")
        self.assertTrue(StockEvent.objects.filter(item=self.masks, kind="count", qty=3).exists())
        # The session is cleared, so a fresh visit starts a brand new stocktake.
        restart = self.client.get("/stocktake/")
        self.assertContains(restart, "Item 1 of 2")

    def test_invalid_step_count_re_renders_with_an_error_and_does_not_advance(self):
        self.client.get("/stocktake/")
        response = self.client.post("/stocktake/save/", {"qty": "abc"})
        self.assertContains(response, "Enter a whole number")
        self.assertContains(response, "Item 1 of 2")
        self.assertFalse(StockEvent.objects.filter(item=self.gloves, kind="count").exists())
