from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from accounts.models import Organisation, User

from .csv_import import parse_csv
from .models import Item, StockEvent, Supplier

HEADER = "name,unit,supplier,price,order_size,count\n"


class ParseCsvTests(TestCase):
    def test_valid_rows_parse_cleanly(self):
        text = HEADER + "Gloves,box,Henry Schein,8.50,10,18\n"
        rows, file_errors = parse_csv(text, {"henry schein"})
        self.assertEqual(file_errors, [])
        self.assertTrue(rows[0].is_valid)
        self.assertEqual(rows[0].name, "Gloves")
        self.assertEqual(str(rows[0].price), "8.50")
        self.assertEqual(rows[0].order_size, 10)
        self.assertEqual(rows[0].count, 18)

    def test_optional_columns_can_be_blank(self):
        text = HEADER + "Gloves,box,Henry Schein,,,\n"
        rows, _ = parse_csv(text, {"henry schein"})
        self.assertTrue(rows[0].is_valid)
        self.assertIsNone(rows[0].price)
        self.assertIsNone(rows[0].order_size)
        self.assertIsNone(rows[0].count)

    def test_missing_required_columns_is_a_file_level_error(self):
        rows, file_errors = parse_csv("name,unit\nGloves,box\n", {"henry schein"})
        self.assertEqual(rows, [])
        self.assertIn("Missing column(s)", file_errors[0])

    def test_missing_name_or_unit_or_supplier_is_a_row_error(self):
        text = HEADER + ",box,Henry Schein,,,\n"
        rows, _ = parse_csv(text, {"henry schein"})
        self.assertFalse(rows[0].is_valid)
        self.assertIn("Name is required.", rows[0].errors)

    def test_unknown_supplier_is_a_row_error(self):
        text = HEADER + "Gloves,box,Nobody Ltd,,,\n"
        rows, _ = parse_csv(text, {"henry schein"})
        self.assertFalse(rows[0].is_valid)
        self.assertIn('No supplier named "Nobody Ltd".', rows[0].errors)

    def test_bad_numbers_are_row_errors(self):
        text = HEADER + "Gloves,box,Henry Schein,abc,0,-1\n"
        rows, _ = parse_csv(text, {"henry schein"})
        self.assertFalse(rows[0].is_valid)
        self.assertEqual(len(rows[0].errors), 3)


class ImportViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)

    def setUp(self):
        self.client.force_login(self.admin)

    def upload(self, text):
        return SimpleUploadedFile("items.csv", text.encode(), content_type="text/csv")

    def test_preview_shows_valid_and_invalid_rows(self):
        text = HEADER + "Gloves,box,Henry Schein,8.50,10,18\nMasks,box,Nobody,,,\n"
        response = self.client.post("/items/import/preview/", {"csv_file": self.upload(text)})
        self.assertContains(response, "Gloves")
        self.assertContains(response, "1 item")
        self.assertContains(response, "No supplier named")
        self.assertFalse(Item.objects.exists())  # nothing saved yet

    def test_confirm_creates_items_and_stock_events(self):
        text = HEADER + "Gloves,box,Henry Schein,8.50,10,18\n"
        self.client.post("/items/import/preview/", {"csv_file": self.upload(text)})
        response = self.client.post("/items/import/confirm/")
        self.assertRedirects(response, "/items/")
        item = Item.objects.get(name="Gloves")
        self.assertEqual(str(item.price), "8.50")
        self.assertEqual(item.order_size, 10)
        self.assertTrue(StockEvent.objects.filter(item=item, kind="count", qty=18).exists())

    def test_confirm_skips_invalid_rows(self):
        text = HEADER + "Gloves,box,Henry Schein,,,\nMasks,box,Nobody,,,\n"
        self.client.post("/items/import/preview/", {"csv_file": self.upload(text)})
        self.client.post("/items/import/confirm/")
        self.assertEqual(Item.objects.count(), 1)
        self.assertTrue(Item.objects.filter(name="Gloves").exists())

    def test_confirm_without_a_prior_preview_shows_an_error(self):
        response = self.client.post("/items/import/confirm/", follow=True)
        self.assertRedirects(response, "/items/import/")
        self.assertContains(response, "Nothing to import")

    def test_preview_without_a_file_redirects_with_an_error(self):
        response = self.client.post("/items/import/preview/", follow=True)
        self.assertRedirects(response, "/items/import/")
        self.assertContains(response, "Choose a CSV file")
