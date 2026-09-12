from datetime import timedelta
from urllib.parse import unquote

from django.contrib.messages import get_messages
from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .forecast import Status, forecast
from .models import Item, OrderLine, StockEvent, Supplier


class ReorderListTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user(
            "sandy@example.com", "pw", name="Sandy", organisation=cls.org, role=User.Role.ADMIN
        )
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.henry = Supplier.objects.create(
            organisation=cls.org, name="Henry Schein", lead_days=5, phone="0800 807 707", email="orders@henryschein.co.nz"
        )
        cls.dentsply = Supplier.objects.create(organisation=cls.org, name="Dentsply", lead_days=7)

        # Out of stock -> wanted.
        cls.gloves = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.henry, price="8.50")
        StockEvent.objects.create(organisation=cls.org, item=cls.gloves, user=cls.admin, kind="count", qty=0)

        # Comfortably OK -> not wanted, unless pinned.
        cls.masks = Item.objects.create(organisation=cls.org, name="Masks", unit="box", supplier=cls.henry)
        StockEvent.objects.create(organisation=cls.org, item=cls.masks, user=cls.admin, kind="count", qty=500)

        # Out of stock at a different supplier.
        cls.ligno = Item.objects.create(organisation=cls.org, name="Lignocaine", unit="cartridge", supplier=cls.dentsply)
        StockEvent.objects.create(organisation=cls.org, item=cls.ligno, user=cls.admin, kind="count", qty=0)

    def test_wanted_items_are_grouped_by_supplier(self):
        self.client.force_login(self.admin)
        content = self.client.get("/reorder/").content.decode()
        self.assertIn("Henry Schein", content)
        self.assertIn("Dentsply", content)
        self.assertIn("Gloves", content)
        self.assertIn("Lignocaine", content)
        self.assertNotIn("Masks", content)

    def test_pinning_an_ok_item_adds_it_to_the_list(self):
        self.masks.pinned_to_reorder_at = timezone.now()
        self.masks.save()
        self.client.force_login(self.admin)
        content = self.client.get("/reorder/").content.decode()
        self.assertIn("Masks", content)

    def test_mailto_link_is_signed_with_admin_and_practice_name(self):
        self.client.force_login(self.admin)
        content = self.client.get("/reorder/").content.decode()
        self.assertIn("Stock%20order%20for%20Henry%20Schein", content)
        decoded = unquote(content)
        self.assertIn("Sandy", decoded)
        self.assertIn("Test Dental", decoded)
        self.assertIn("Gloves", decoded)

    def test_assistant_sees_read_only_waiting_message_and_no_price(self):
        self.client.force_login(self.assistant)
        response = self.client.get("/reorder/")
        content = response.content.decode()
        self.assertIn("waiting for the manager to order", content)
        self.assertIn("Gloves", content)
        self.assertNotIn("Mark ordered", content)
        self.assertNotRegex(content, r"\$\s?\d")  # a real price, not Alpine's $event

    def test_mark_ordered_creates_an_order_line_and_clears_the_pin(self):
        self.masks.pinned_to_reorder_at = timezone.now()
        self.masks.save()
        self.client.force_login(self.admin)
        response = self.client.post(f"/reorder/item/{self.gloves.pk}/ordered/", {f"qty_{self.gloves.pk}": "20"})
        self.assertEqual(response["HX-Redirect"], "/reorder/")
        order = OrderLine.objects.get(item=self.gloves)
        self.assertEqual(order.qty, 20)
        self.assertEqual(str(order.unit_price), "8.50")
        self.assertEqual(order.ordered_by, self.admin)
        self.assertTrue(order.is_open)

    def test_ordering_moves_the_item_to_on_order_status(self):
        self.client.force_login(self.admin)
        self.client.post(f"/reorder/item/{self.gloves.pk}/ordered/", {f"qty_{self.gloves.pk}": "20"})
        self.gloves.refresh_from_db()
        events = list(self.gloves.events.all())
        open_orders = [o for o in self.gloves.order_lines.all() if o.is_open]
        f = forecast(events, open_orders, lead_days=5)
        self.assertEqual(f.status, Status.ON_ORDER)

    def test_mark_supplier_ordered_orders_every_wanted_item_for_that_supplier(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/reorder/supplier/{self.henry.pk}/ordered/", {f"qty_{self.gloves.pk}": "15"}
        )
        self.assertEqual(response["HX-Redirect"], "/reorder/")
        self.assertEqual(OrderLine.objects.filter(item__supplier=self.henry).count(), 1)
        self.assertEqual(OrderLine.objects.get(item=self.gloves).qty, 15)
        # Dentsply's item is untouched.
        self.assertFalse(OrderLine.objects.filter(item=self.ligno).exists())

    def test_can_order_from_both_suppliers(self):
        self.client.force_login(self.admin)
        self.client.post(f"/reorder/supplier/{self.henry.pk}/ordered/")
        self.client.post(f"/reorder/supplier/{self.dentsply.pk}/ordered/")
        self.assertTrue(OrderLine.objects.filter(item=self.gloves, cancelled_at__isnull=True).exists())
        self.assertTrue(OrderLine.objects.filter(item=self.ligno, cancelled_at__isnull=True).exists())


class ReorderUndoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.other_admin = User.objects.create_user("owner@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def order(self, **kwargs):
        return OrderLine.objects.create(organisation=self.org, item=self.item, qty=10, ordered_by=self.admin, **kwargs)

    def test_undo_cancels_a_recent_order(self):
        order = self.order()
        self.client.force_login(self.admin)
        response = self.client.post(f"/reorder/undo/{order.pk}/")
        self.assertEqual(response.status_code, 204)
        order.refresh_from_db()
        self.assertIsNotNone(order.cancelled_at)

    def test_any_admin_can_undo_not_just_the_one_who_ordered(self):
        order = self.order()
        self.client.force_login(self.other_admin)
        self.client.post(f"/reorder/undo/{order.pk}/")
        order.refresh_from_db()
        self.assertIsNotNone(order.cancelled_at)

    def test_undo_refuses_an_order_older_than_ten_minutes(self):
        order = self.order(ordered_at=timezone.now() - timedelta(minutes=11))
        self.client.force_login(self.admin)
        response = self.client.post(f"/reorder/undo/{order.pk}/")
        self.assertEqual(response.status_code, 403)
        order.refresh_from_db()
        self.assertIsNone(order.cancelled_at)

    def test_undo_batch_cancels_all_listed_orders(self):
        order_a = self.order()
        item_b = Item.objects.create(organisation=self.org, name="Masks", unit="box", supplier=self.supplier)
        order_b = OrderLine.objects.create(organisation=self.org, item=item_b, qty=5, ordered_by=self.admin)
        self.client.force_login(self.admin)
        self.client.post(f"/reorder/undo-batch/?ids={order_a.pk},{order_b.pk}")
        order_a.refresh_from_db()
        order_b.refresh_from_db()
        self.assertIsNotNone(order_a.cancelled_at)
        self.assertIsNotNone(order_b.cancelled_at)

    def test_batch_undo_message_extra_tags_round_trip(self):
        self.client.force_login(self.admin)
        response = self.client.post(f"/reorder/item/{self.item.pk}/ordered/")
        stored_messages = list(get_messages(response.wsgi_request))
        self.assertTrue(stored_messages[0].extra_tags.startswith("/reorder/undo/"))
