from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .forecast import Status, forecast
from .models import Item, OrderLine, StockEvent, Supplier
from .views import _median_lead_days


class DeliveriesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(
            organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier, price="8.50"
        )
        StockEvent.objects.create(organisation=cls.org, item=cls.item, user=cls.admin, kind="count", qty=0)
        cls.order = OrderLine.objects.create(organisation=cls.org, item=cls.item, qty=20, ordered_by=cls.admin)

    def test_list_shows_the_open_line_grouped_by_supplier(self):
        self.client.force_login(self.admin)
        content = self.client.get("/deliveries/").content.decode()
        self.assertIn("Henry Schein", content)
        self.assertIn("Gloves", content)
        self.assertIn("Ordered 20 boxes", content)

    def test_a_line_past_its_expected_date_is_flagged_late(self):
        self.order.expected_at = timezone.now() - timedelta(days=1)
        self.order.save()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/deliveries/"), "Late")

    def test_receiving_in_full_writes_a_received_event_and_closes_the_line(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/", {"receive_line": self.order.pk, f"qty_{self.order.pk}": "20"}
        )
        self.assertRedirects(response, "/deliveries/")
        self.order.refresh_from_db()
        self.assertEqual(self.order.received_qty, 20)
        self.assertEqual(self.order.received_by, self.admin)
        self.assertFalse(self.order.is_open)
        event = StockEvent.objects.get(item=self.item, kind="received")
        self.assertEqual(event.qty, 20)
        self.assertFalse(OrderLine.objects.filter(item=self.item).exclude(pk=self.order.pk).exists())

    def test_receiving_sets_the_item_back_to_ok_and_off_the_reorder_list(self):
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/", {"receive_line": self.order.pk, f"qty_{self.order.pk}": "20"}
        )
        events = list(self.item.events.all())
        open_orders = [o for o in self.item.order_lines.all() if o.is_open]
        f = forecast(events, open_orders, lead_days=5)
        self.assertEqual(f.status, Status.OK)

    def test_partial_receipt_splits_off_a_back_ordered_remainder(self):
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/", {"receive_line": self.order.pk, f"qty_{self.order.pk}": "12"}
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.qty, 20)
        self.assertEqual(self.order.received_qty, 12)
        self.assertFalse(self.order.is_open)

        remainder = OrderLine.objects.get(item=self.item, received_at__isnull=True)
        self.assertEqual(remainder.qty, 8)
        self.assertTrue(remainder.is_open)
        self.assertEqual(remainder.ordered_by, self.admin)
        self.assertEqual(remainder.split_from, self.order)

        event = StockEvent.objects.get(item=self.item, kind="received")
        self.assertEqual(event.qty, 12)

    def test_partial_receipt_says_the_status_in_words(self):
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/", {"receive_line": self.order.pk, f"qty_{self.order.pk}": "12"}
        )
        deliveries = self.client.get("/deliveries/")
        self.assertContains(deliveries, "Part delivered")
        self.assertContains(deliveries, "12 of 20 arrived, 8 still coming")

        item_page = self.client.get(f"/item/{self.item.pk}/")
        self.assertContains(item_page, "Part delivered")
        self.assertContains(item_page, "12 of 20 arrived, 8 still coming")

    def test_partial_receipt_item_stays_on_order_for_the_remainder(self):
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/", {"receive_line": self.order.pk, f"qty_{self.order.pk}": "12"}
        )
        events = list(self.item.events.all())
        open_orders = [o for o in self.item.order_lines.all() if o.is_open]
        f = forecast(events, open_orders, lead_days=5)
        self.assertEqual(f.status, Status.ON_ORDER)
        self.assertEqual(f.incoming, 8)

    def test_receive_all_receives_every_open_line_for_the_supplier(self):
        masks = Item.objects.create(organisation=self.org, name="Masks", unit="box", supplier=self.supplier)
        order2 = OrderLine.objects.create(organisation=self.org, item=masks, qty=5, ordered_by=self.admin)
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/",
            {"receive_all": self.supplier.pk, f"qty_{self.order.pk}": "20", f"qty_{order2.pk}": "5"},
        )
        self.order.refresh_from_db()
        order2.refresh_from_db()
        self.assertFalse(self.order.is_open)
        self.assertFalse(order2.is_open)

    def test_admin_can_correct_the_unit_price_which_updates_the_item(self):
        self.client.force_login(self.admin)
        self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/",
            {"receive_line": self.order.pk, f"qty_{self.order.pk}": "20", f"price_{self.order.pk}": "9.25"},
        )
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(str(self.order.unit_price), "9.25")
        self.assertEqual(str(self.item.price), "9.25")

    def test_assistant_can_receive_but_cannot_change_the_price(self):
        self.client.force_login(self.assistant)
        response = self.client.post(
            f"/deliveries/supplier/{self.supplier.pk}/",
            {"receive_line": self.order.pk, f"qty_{self.order.pk}": "20", f"price_{self.order.pk}": "999.00"},
        )
        self.assertRedirects(response, "/deliveries/")
        self.order.refresh_from_db()
        self.item.refresh_from_db()
        self.assertFalse(self.order.is_open)
        self.assertIsNone(self.order.unit_price)
        self.assertEqual(str(self.item.price), "8.50")

    def test_assistant_sees_no_price_input(self):
        self.client.force_login(self.assistant)
        content = self.client.get("/deliveries/").content.decode()
        self.assertNotRegex(content, r"\$\s?\d")
        self.assertNotIn(f"price_{self.order.pk}", content)


class LeadTimeLearningTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def receive(self, ordered_days_ago, lead_days):
        ordered_at = timezone.now() - timedelta(days=ordered_days_ago)
        OrderLine.objects.create(
            organisation=self.org,
            item=self.item,
            qty=1,
            ordered_by=self.admin,
            ordered_at=ordered_at,
            received_at=ordered_at + timedelta(days=lead_days),
            received_qty=1,
            received_by=self.admin,
        )

    def test_median_of_actual_lead_times(self):
        self.receive(30, 4)
        self.receive(20, 6)
        self.receive(10, 8)
        self.assertEqual(_median_lead_days(self.supplier), 6)

    def test_no_received_orders_gives_no_suggestion(self):
        self.assertIsNone(_median_lead_days(self.supplier))

    def test_only_the_last_ten_orders_count(self):
        for i in range(12):
            self.receive(100 - i, 1)  # 12 old fast ones
        self.receive(1, 20)  # one very recent slow one
        # The slow one plus the 9 most recent fast ones (10 total) -> median tips towards fast.
        self.assertLess(_median_lead_days(self.supplier), 20)

    def test_use_this_button_updates_lead_days(self):
        self.receive(10, 9)
        self.client.force_login(self.admin)
        self.client.post(f"/suppliers/{self.supplier.pk}/apply-lead-days/")
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 9)

    def test_use_this_does_nothing_without_data(self):
        self.client.force_login(self.admin)
        self.client.post(f"/suppliers/{self.supplier.pk}/apply-lead-days/")
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 5)
