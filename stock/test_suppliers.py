from django.contrib.messages import get_messages
from django.test import TestCase

from accounts.models import Organisation, User

from .models import Item, Supplier


class SupplierManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.item = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.supplier)

    def setUp(self):
        self.client.force_login(self.admin)

    def test_list_shows_the_supplier_with_its_active_item_count(self):
        content = self.client.get("/suppliers/").content.decode()
        self.assertIn("Henry Schein", content)
        self.assertIn("1 item", content)
        self.assertIn("5 days", content)

    def test_add_a_supplier(self):
        response = self.client.post(
            "/suppliers/add/", {"name": "Dentsply", "lead_days": "7", "phone": "0800 335 626", "email": "orders@dentsply.co.nz"}
        )
        self.assertRedirects(response, "/suppliers/")
        supplier = Supplier.objects.get(name="Dentsply")
        self.assertEqual(supplier.lead_days, 7)
        self.assertEqual(supplier.organisation, self.org)

    def test_lead_days_must_be_between_1_and_60(self):
        for value in ["0", "61", "-1"]:
            with self.subTest(value):
                response = self.client.post("/suppliers/add/", {"name": "New", "lead_days": value})
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Supplier.objects.filter(name="New").exists())

    def test_invalid_email_is_rejected(self):
        response = self.client.post(
            "/suppliers/add/", {"name": "New", "lead_days": "5", "email": "not-an-email"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Supplier.objects.filter(name="New").exists())

    def test_duplicate_name_is_case_insensitively_rejected(self):
        response = self.client.post("/suppliers/add/", {"name": "henry schein", "lead_days": "5"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Supplier.objects.filter(name__iexact="henry schein").count(), 1)

    def test_edit_form_opens_and_updates_the_row(self):
        content = self.client.get(f"/suppliers/{self.supplier.pk}/edit/").content.decode()
        self.assertIn('value="Henry Schein"', content)

        response = self.client.post(
            f"/suppliers/{self.supplier.pk}/update/",
            {"name": "Henry Schein NZ", "lead_days": "6", "phone": "0800 807 707", "email": "orders@henryschein.co.nz"},
        )
        self.assertContains(response, "Henry Schein NZ")
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.name, "Henry Schein NZ")
        self.assertEqual(self.supplier.lead_days, 6)

    def test_invalid_edit_re_renders_the_row_with_an_error(self):
        response = self.client.post(f"/suppliers/{self.supplier.pk}/update/", {"name": "Henry Schein", "lead_days": "99"})
        self.assertEqual(response.status_code, 200)
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 5)

    def test_lead_days_stepper_increments_and_decrements(self):
        self.client.post(f"/suppliers/{self.supplier.pk}/lead-days/", {"direction": "up"})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 6)

        self.client.post(f"/suppliers/{self.supplier.pk}/lead-days/", {"direction": "down"})
        self.client.post(f"/suppliers/{self.supplier.pk}/lead-days/", {"direction": "down"})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 4)

    def test_lead_days_stepper_is_clamped_between_1_and_60(self):
        self.supplier.lead_days = 1
        self.supplier.save()
        self.client.post(f"/suppliers/{self.supplier.pk}/lead-days/", {"direction": "down"})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 1)

        self.supplier.lead_days = 60
        self.supplier.save()
        self.client.post(f"/suppliers/{self.supplier.pk}/lead-days/", {"direction": "up"})
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 60)

    def test_cannot_archive_a_supplier_with_active_items(self):
        response = self.client.post(f"/suppliers/{self.supplier.pk}/archive/")
        self.assertEqual(response["HX-Redirect"], "/suppliers/")
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)

    def test_archiving_hides_the_supplier_and_can_be_undone(self):
        self.item.is_active = False
        self.item.save()

        response = self.client.post(f"/suppliers/{self.supplier.pk}/archive/")
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.is_active)
        stored_messages = list(get_messages(response.wsgi_request))
        undo_url = stored_messages[0].extra_tags

        # A fresh request (past request's toast already consumed) shows the
        # archived supplier is gone from the list, not just flagged inactive.
        self.assertContains(self.client.get("/suppliers/"), "No suppliers yet")

        self.client.post(undo_url)
        self.supplier.refresh_from_db()
        self.assertTrue(self.supplier.is_active)
