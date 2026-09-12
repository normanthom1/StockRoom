from django.contrib.messages import get_messages
from django.test import TestCase

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
