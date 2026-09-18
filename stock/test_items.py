from datetime import timedelta

from django.contrib.messages import get_messages
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import Organisation, User

from .models import Item, StockEvent, Supplier


class ItemManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_list_shows_the_item(self):
        content = self.client.get("/items/").content.decode()
        self.assertIn("Gloves", content)
        self.assertIn("Henry Schein", content)

    def test_search_filters_the_list(self):
        Item.objects.create(organisation=self.org, name="Masks", unit="box", supplier=self.supplier)
        response = self.client.get("/items/?q=glov")
        self.assertContains(response, "Gloves")
        self.assertNotContains(response, "Masks")

    def test_add_item_with_starting_count_writes_a_count_event(self):
        response = self.client.post(
            "/items/add/",
            {"name": "Masks", "unit": "box", "supplier": self.supplier.pk, "price": "12.50", "order_size": "10", "starting_count": "20"},
        )
        self.assertRedirects(response, "/items/")
        item = Item.objects.get(name="Masks")
        self.assertEqual(item.order_size, 10)
        event = StockEvent.objects.get(item=item, kind="count")
        self.assertEqual(event.qty, 20)
        self.assertEqual(event.user, self.admin)

    def test_add_item_without_starting_count_has_no_history(self):
        self.client.post("/items/add/", {"name": "Masks", "unit": "box", "supplier": self.supplier.pk})
        item = Item.objects.get(name="Masks")
        self.assertFalse(StockEvent.objects.filter(item=item).exists())

    def test_new_items_usage_rate_shows_still_learning_on_home(self):
        self.client.post("/items/add/", {"name": "Masks", "unit": "box", "supplier": self.supplier.pk})
        content = self.client.get("/").content.decode()
        self.assertIn("still learning", content)

    def test_supplier_dropdown_cannot_be_used_to_pick_another_practices_supplier(self):
        other_org = Organisation.objects.create(name="Other Dental")
        other_supplier = Supplier.objects.create(organisation=other_org, name="Someone Else's Supplier")
        response = self.client.post(
            "/items/add/", {"name": "Masks", "unit": "box", "supplier": other_supplier.pk}
        )
        self.assertEqual(response.status_code, 200)  # re-rendered with a form error
        self.assertFalse(Item.objects.filter(name="Masks").exists())

    def test_edit_item_updates_fields(self):
        content = self.client.get(f"/items/{self.item.pk}/edit/").content.decode()
        self.assertIn('value="Gloves"', content)

        other_supplier = Supplier.objects.create(organisation=self.org, name="Dentsply", lead_days=7)
        response = self.client.post(
            f"/items/{self.item.pk}/update/",
            {"name": "Nitrile gloves", "unit": "box", "supplier": other_supplier.pk, "price": "9.00"},
        )
        self.assertContains(response, "Nitrile gloves")
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, "Nitrile gloves")
        self.assertEqual(self.item.supplier, other_supplier)

    def test_archiving_hides_the_item_and_can_be_undone(self):
        response = self.client.post(f"/items/{self.item.pk}/archive/")
        self.assertEqual(response["HX-Redirect"], "/items/")
        self.item.refresh_from_db()
        self.assertFalse(self.item.is_active)
        self.assertContains(self.client.get("/items/"), "No items yet.")

        stored_messages = list(get_messages(response.wsgi_request))
        undo_url = stored_messages[0].extra_tags
        self.client.post(undo_url)
        self.item.refresh_from_db()
        self.assertTrue(self.item.is_active)


class StockListStatusTests(TestCase):
    """UX-07. The page called Stock listed name, unit, supplier and price and no
    status at all, so "is anything close to running out that I haven't been told
    about?" could only be answered by holding Home in your head while scrolling."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)

    def setUp(self):
        self.client.force_login(self.admin)

    def stocked(self, name, on_hand, used_per_week=0):
        """An item with enough history for the forecast to have an opinion."""
        item = Item.objects.create(organisation=self.org, name=name, unit="box", supplier=self.supplier)
        StockEvent.objects.create(organisation=self.org, item=item, user=self.admin, kind="count",
                                  qty=on_hand + used_per_week * 4,
                                  created_at=timezone.now() - timedelta(weeks=4))
        for week in range(4):
            if used_per_week:
                StockEvent.objects.create(organisation=self.org, item=item, user=self.admin, kind="used",
                                          qty=used_per_week,
                                          created_at=timezone.now() - timedelta(weeks=3 - week))
        return item

    def test_an_item_that_has_run_out_says_so_on_the_stock_page(self):
        self.stocked("Suction tips", on_hand=0, used_per_week=35)

        response = self.client.get("/items/")

        self.assertContains(response, "Out of stock")
        self.assertContains(response, "bg-status-out")  # the left stripe

    def test_the_status_matches_what_home_says_about_the_same_item(self):
        self.stocked("Suction tips", on_hand=0, used_per_week=35)

        home = self.client.get("/").context["rows"][0]
        stock_row = self.client.get("/items/").context["item_list"][0].status

        self.assertEqual(stock_row["status_label"], home["status_label"])
        self.assertEqual(stock_row["status_color"], home["status_color"])

    def test_an_item_with_nothing_to_do_gets_a_figure_but_no_chip(self):
        """An "OK" chip on every row is noise, and "~214 days" is false precision."""
        self.stocked("Bib clips", on_hand=500, used_per_week=1)

        row = self.client.get("/items/").context["item_list"][0].status

        self.assertFalse(row["needs_doing"])
        self.assertNotIn("days", row["fine_label"])  # "6 months+", not "~214 days"

    def test_searching_keeps_the_status_on_what_is_left(self):
        self.stocked("Suction tips", on_hand=0, used_per_week=35)
        self.stocked("Bib clips", on_hand=500, used_per_week=1)

        response = self.client.get("/items/?q=suction", headers={"HX-Request": "true"})

        self.assertContains(response, "Out of stock")
        self.assertNotContains(response, "Bib clips")

    def test_a_row_keeps_its_status_after_an_edit_is_saved(self):
        item = self.stocked("Suction tips", on_hand=0, used_per_week=35)

        response = self.client.post(f"/items/{item.pk}/update/",
                                    {"name": "Suction tips, disposable", "unit": "tip",
                                     "supplier": self.supplier.pk})

        self.assertContains(response, "Out of stock")

    def test_the_page_does_not_query_once_per_item(self):
        """The forecast needs every item's history, so this is the page's one
        real N+1 risk. _org_items prefetches it; without that, 25 items would
        cost 25 extra queries and the page would crawl for a real practice."""
        for n in range(5):
            self.stocked(f"Item {n}", on_hand=10, used_per_week=1)
        self.client.get("/items/")  # warm any one-off lookups
        with CaptureQueriesContext(connection) as small:
            self.client.get("/items/")

        for n in range(5, 25):
            self.stocked(f"Item {n}", on_hand=10, used_per_week=1)
        with CaptureQueriesContext(connection) as large:
            self.client.get("/items/")

        self.assertEqual(len(large.captured_queries), len(small.captured_queries),
                         "queries grew with the number of items")
