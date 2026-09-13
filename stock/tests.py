import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import Client, TestCase

from accounts.models import Organisation, User

from .models import Item, OrderLine, StockEvent, Supplier


class StockConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.user = User.objects.create_user("liz@example.com", "pw", organisation=cls.org)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=4)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def event(self, **kwargs):
        return StockEvent.objects.create(organisation=self.org, item=self.item, user=self.user, **kwargs)

    def assert_rejected(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def test_count_used_and_received_need_a_qty(self):
        for kind in ["count", "used", "received"]:
            with self.subTest(kind):
                self.assert_rejected(lambda k=kind: self.event(kind=k))
                self.event(kind=kind, qty=3)

    def test_low_and_out_need_no_qty(self):
        self.event(kind="low")
        self.event(kind="out")

    def test_qty_cannot_be_negative(self):
        self.assert_rejected(lambda: self.event(kind="count", qty=-1))
        self.assert_rejected(
            lambda: OrderLine.objects.create(organisation=self.org, item=self.item, qty=-1, ordered_by=self.user)
        )

    def test_client_id_dedupes_offline_replays(self):
        client_id = uuid.uuid4()
        self.event(kind="used", qty=1, client_id=client_id)
        self.assert_rejected(lambda: self.event(kind="used", qty=1, client_id=client_id))
        # Online taps send no client_id, and any number of them can be null.
        self.event(kind="used", qty=1)
        self.event(kind="used", qty=1)

    def test_supplier_names_are_unique_per_practice(self):
        self.assert_rejected(lambda: Supplier.objects.create(organisation=self.org, name="henry schein"))
        other = Organisation.objects.create(name="Other Dental")
        Supplier.objects.create(organisation=other, name="Henry Schein")

    def test_supplier_with_items_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.supplier.delete()

    def test_order_line_expects_delivery_after_lead_days(self):
        line = OrderLine.objects.create(organisation=self.org, item=self.item, qty=10, ordered_by=self.user)
        self.assertEqual(line.expected_at, line.ordered_at + timedelta(days=4))
        self.assertTrue(line.is_open)


class HomeSmokeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organisation.objects.create(name="Test Dental")
        cls.user = User.objects.create_user("liz@example.com", "pw", organisation=org)

    def setUp(self):
        self.client.force_login(self.user)

    def test_home_page_loads(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "What to order today")
        self.assertContains(response, "vendor/htmx.min.js")
        self.assertContains(response, "vendor/alpine-csp.min.js")
        self.assertContains(response, "js/app.js")

    def test_csrf_is_enforced_on_posts(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post("/log-usage/undo/1/")
        self.assertEqual(response.status_code, 403)


class AppShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=org, role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("liz@example.com", "pw", organisation=org)

    def test_assistant_nav_has_no_admin_only_links(self):
        self.client.force_login(self.assistant)
        response = self.client.get("/")
        content = response.content.decode()
        for label in ["Home", "Log usage", "Reorder list", "Deliveries"]:
            self.assertContains(response, label)
        self.assertNotIn(">More<", content)
        self.assertNotIn("/items/", content)
        self.assertNotIn("/suppliers/", content)
        self.assertNotIn("/spending/", content)

    def test_admin_nav_has_the_overflow_menu(self):
        self.client.force_login(self.admin)
        response = self.client.get("/")
        self.assertContains(response, "More")
        self.assertContains(response, 'href="/items/"')
        self.assertContains(response, 'href="/suppliers/"')
        self.assertContains(response, 'href="/spending/"')
        self.assertContains(response, 'href="/accounts/team/"')
