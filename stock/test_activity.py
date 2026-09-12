import csv
import io

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .models import Item, OrderLine, StockEvent, Supplier


class ActivityLogTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user(
            "sandy@example.com", "pw", name="Sandy", organisation=cls.org, role=User.Role.ADMIN
        )
        cls.assistant = User.objects.create_user(
            "liz@example.com", "pw", name="Liz", organisation=cls.org
        )
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.gloves = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)
        cls.masks = Item.objects.create(organisation=cls.org, name="Masks", unit="box", supplier=cls.supplier)

        cls.event = StockEvent.objects.create(
            organisation=cls.org, item=cls.gloves, user=cls.assistant, kind="used", qty=1
        )
        cls.order = OrderLine.objects.create(
            organisation=cls.org, item=cls.masks, qty=10, ordered_by=cls.admin,
            received_qty=10, received_at=timezone.now(), received_by=cls.assistant,
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_shows_stock_events_and_orders(self):
        content = self.client.get("/activity/").content.decode()
        self.assertIn("Liz", content)
        self.assertIn("Used 1 Gloves", content)
        self.assertIn("Sandy", content)
        self.assertIn("Ordered 10 boxes of Masks", content)
        self.assertIn("Received 10 boxes of Masks", content)

    def test_filter_by_item(self):
        response = self.client.get(f"/activity/?item={self.gloves.pk}")
        self.assertContains(response, "Used 1 Gloves")
        # "Masks" still appears once, in the filter dropdown's own option -
        # just not as part of any activity entry.
        self.assertNotContains(response, "of Masks")

    def test_filter_by_user(self):
        response = self.client.get(f"/activity/?user={self.assistant.pk}")
        content = response.content.decode()
        self.assertIn("Gloves", content)  # Liz's used-tap
        self.assertIn("Received", content)  # Liz received the delivery
        self.assertNotIn("Ordered", content)  # Sandy placed the order, not Liz

    def test_pagination(self):
        for _ in range(30):
            StockEvent.objects.create(organisation=self.org, item=self.gloves, user=self.assistant, kind="used", qty=1)
        page1 = self.client.get("/activity/")
        self.assertContains(page1, "Older")
        page2 = self.client.get("/activity/?page=2")
        self.assertEqual(page2.status_code, 200)
        self.assertContains(page2, "Newer")


class ExportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(
            organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier, price="8.50"
        )
        StockEvent.objects.create(organisation=cls.org, item=cls.item, user=cls.admin, kind="count", qty=10)
        OrderLine.objects.create(organisation=cls.org, item=cls.item, qty=5, ordered_by=cls.admin)

        cls.other_org = Organisation.objects.create(name="Other Dental")
        cls.other_admin = User.objects.create_user(
            "other@example.com", "pw", organisation=cls.other_org, role=User.Role.ADMIN
        )
        other_supplier = Supplier.objects.create(organisation=cls.other_org, name="Dentsply", lead_days=7)
        cls.other_item = Item.objects.create(
            organisation=cls.other_org, name="Secret Item", unit="box", supplier=other_supplier
        )
        StockEvent.objects.create(organisation=cls.other_org, item=cls.other_item, user=cls.other_admin, kind="count", qty=1)
        OrderLine.objects.create(organisation=cls.other_org, item=cls.other_item, qty=1, ordered_by=cls.other_admin)

    def setUp(self):
        self.client.force_login(self.admin)

    def _rows(self, response):
        text = response.content.decode("utf-8-sig")
        return list(csv.reader(io.StringIO(text)))

    def test_export_items_is_a_csv_scoped_to_the_organisation(self):
        response = self.client.get("/export/items.csv")
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment", response["Content-Disposition"])
        rows = self._rows(response)
        self.assertEqual(rows[0], ["name", "unit", "supplier", "price", "order_size", "active"])
        names = [row[0] for row in rows[1:]]
        self.assertIn("Gloves", names)
        self.assertNotIn("Secret Item", names)

    def test_export_has_a_byte_order_mark_for_excel(self):
        response = self.client.get("/export/items.csv")
        self.assertTrue(response.content.startswith(b"\xef\xbb\xbf"))

    def test_export_stock_events_scoped_to_the_organisation(self):
        rows = self._rows(self.client.get("/export/stock-events.csv"))
        items = [row[1] for row in rows[1:]]
        self.assertIn("Gloves", items)
        self.assertNotIn("Secret Item", items)

    def test_export_order_lines_scoped_to_the_organisation(self):
        rows = self._rows(self.client.get("/export/order-lines.csv"))
        items = [row[1] for row in rows[1:]]
        self.assertIn("Gloves", items)
        self.assertNotIn("Secret Item", items)

    def test_another_organisations_admin_cannot_see_this_orgs_data(self):
        self.client.force_login(self.other_admin)
        rows = self._rows(self.client.get("/export/items.csv"))
        names = [row[0] for row in rows[1:]]
        self.assertIn("Secret Item", names)
        self.assertNotIn("Gloves", names)
