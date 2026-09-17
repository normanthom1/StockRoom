from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organisation, User

from .invoices import parse_invoice, save_invoice
from .matching import Matcher, match_invoice_lines, normalize, synonym_key
from .models import Item, ItemAlias, Supplier


class NormalizeTests(SimpleTestCase):
    def test_variants_of_one_product_share_a_key(self):
        same = [
            ("Syringe 5ml", "5 ml syringe"),
            ("glove", "Gloves"),
            ("Nitrile gloves, size M", "size M nitrile glove"),
            ("Nitrile gloves 100/box", "Box of 100 nitrile gloves"),
            ("Nitrile gloves x100", "nitrile gloves 100 pieces"),
            ("Lignocaine 2.2mL", "lignocaine 2.2 millilitres"),
            ("Alginate 500 g", "alginate 500grams"),
            ("Burs, #330", "burs 330"),
            ("Autoclave pouches", "autoclave pouch"),
            ("Brushes", "brush"),
            ("Supplies", "supply"),
            ("Pouches for the autoclave", "autoclave pouches"),
            ("Prophy-paste (mint)", "prophy paste mint"),
            ("Face shield", "FACE SHIELDS"),
            ("Boxes", "box"),
            ("Glasses", "glass"),
        ]
        for a, b in same:
            with self.subTest(a=a, b=b):
                self.assertEqual(normalize(a), normalize(b))

    def test_different_products_keep_different_keys(self):
        for a, b in [("Syringe 5ml", "Syringe 10ml"), ("Autoclave pouches 57x130", "Autoclave pouches 90x230"),
                     ("Glass", "Gas"), ("Nitrile gloves, size M", "Nitrile gloves, size L")]:
            with self.subTest(a=a, b=b):
                self.assertNotEqual(normalize(a), normalize(b))

    def test_synonyms_swap_in_the_main_name(self):
        self.assertEqual(synonym_key("Patient napkins"), synonym_key("Dental bibs"))
        self.assertEqual(synonym_key("Lidocaine 2% carpules"), synonym_key("Lignocaine 2% cartridges"))
        self.assertEqual(synonym_key("GIC capsules"), synonym_key("Glass ionomer cement capsules"))
        self.assertNotEqual(normalize("Patient napkins"), normalize("Dental bibs"))


class MatcherTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.schein = Supplier.objects.create(organisation=cls.org, name="Henry Schein")
        cls.other = Supplier.objects.create(organisation=cls.org, name="Aluro Healthcare")
        cls.gloves = cls.item("Gloves")
        cls.strips = cls.item("Polishing strips")
        cls.syringe = cls.item("Syringe 5ml")
        cls.pellets = cls.item("Cotton pellets")

    @classmethod
    def item(cls, name, supplier=None, **fields):
        return Item.objects.create(organisation=cls.org, name=name, unit="box", supplier=supplier or cls.schein, **fields)

    def invoice(self, description="glove"):
        csv = f"vendor,sku,description,qty,unit\nHenry Schein,,{description},2,8.50\n".encode()
        return save_invoice(*parse_invoice(self.org, csv, "text/csv"))

    def match(self, name, **kwargs):
        return Matcher(self.org).match(name, **kwargs)

    def test_an_invoice_line_matches_an_existing_item_and_is_remembered(self):
        invoice = self.invoice()
        items_before = Item.objects.count()
        [(line, match)] = match_invoice_lines(invoice, self.admin)
        self.assertEqual((match.item, match.band), (self.gloves, "sure"))
        line.refresh_from_db()
        self.assertEqual(line.item, self.gloves)
        self.assertEqual(Item.objects.count(), items_before)
        alias = ItemAlias.objects.get()
        self.assertEqual((alias.item, alias.method, alias.confidence), (self.gloves, "exact", 1.0))
        self.assertEqual((alias.source, alias.source_invoice, alias.raw_name), ("invoice", invoice, "glove"))

    def test_matching_again_adds_no_second_alias(self):
        invoice = self.invoice()
        match_invoice_lines(invoice, self.admin)
        match_invoice_lines(invoice, self.admin)
        [(line, match)] = match_invoice_lines(self.invoice(), self.admin)  # the same invoice, imported again
        self.assertEqual((line.item, match.method), (self.gloves, "alias"))
        self.assertEqual(ItemAlias.objects.count(), 1)

    def test_the_suppliers_code_beats_a_better_name(self):
        coded = self.item("Blue box of things", supplier_sku="HS-GLV")
        self.assertEqual(self.match("Gloves", sku="hs-glv", supplier=self.schein).item, coded)
        self.assertEqual(self.match("Gloves", sku="HS-GLV", supplier=self.other).item, self.gloves)

    def test_ties_go_to_the_code_then_supplier_then_price_and_are_never_sure(self):
        aluro = self.item("gloves", supplier=self.other, price="9.00")
        match = self.match("Glove", supplier=self.other)
        self.assertEqual((match.item, match.band), (aluro, "likely"))
        self.assertEqual(self.match("Glove", supplier=self.schein).item, self.gloves)

    def test_a_different_size_or_a_vaguer_name_is_never_sure(self):
        self.assertEqual(self.match("Syringe 10ml").band, "check")
        match = self.match("Nitrile gloves, size M")
        self.assertEqual((match.item, match.band, match.label), (self.gloves, "likely", "Likely match – 94%"))

    def test_the_threshold_setting_decides_a_close_spelling(self):
        self.assertEqual(self.match("Polish strips").band, "likely")
        with override_settings(PRODUCT_FUZZY_THRESHOLD=0.9):
            self.assertEqual(self.match("Polish strips").band, "check")

    def test_nothing_like_it_is_no_match(self):
        match = self.match("Rubber dam clamps")
        self.assertIsNone(match.item)
        self.assertEqual(match.band, "")

    def test_embeddings_only_check_unsure_names_and_only_with_ai(self):
        embed = mock.patch("assistant.gemini.embed", side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
        with embed as called:
            unsure, sure = Matcher(self.org).match_all([{"name": "Cotton rolls"}, {"name": "Gloves"}])
        called.assert_not_called()
        self.assertEqual((unsure.item, unsure.band), (self.pellets, "check"))

        with override_settings(AI_API_KEY="test-key"), embed as called:
            unsure, sure = Matcher(self.org).match_all([{"name": "Cotton rolls"}, {"name": "Gloves"}])
            Matcher(self.org).match_all([{"name": "Cotton rolls"}])
        self.assertEqual(called.call_args_list[0].args[0], ["Cotton rolls", "Cotton pellets"])
        self.assertEqual(called.call_args_list[1].args[0], ["Cotton rolls"])  # the item's vector was cached
        self.assertEqual((unsure.item, unsure.method, unsure.band), (self.pellets, "semantic", "likely"))
        self.assertEqual(sure.method, "exact")

    def test_undo_unmatches_the_line_and_stops_it_matching_again(self):
        invoice = self.invoice()
        [(line, _)] = match_invoice_lines(invoice, self.admin)
        alias = ItemAlias.objects.get()
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/items/matched/"), "glove &rarr; Gloves")

        self.assertEqual(self.client.post(f"/items/matched/{alias.pk}/undo/").status_code, 200)
        alias.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual((alias.reverted_by, line.item), (self.admin, None))
        [(line, match)] = match_invoice_lines(self.invoice(), self.admin)
        self.assertIsNone(line.item)
        self.assertNotEqual(match.item, self.gloves)
        self.assertEqual(self.client.post(f"/items/matched/{alias.pk}/undo/").status_code, 403)

    def test_undo_lasts_30_days(self):
        match_invoice_lines(self.invoice(), self.admin)
        alias = ItemAlias.objects.get()
        self.client.force_login(self.admin)
        ItemAlias.objects.update(created_at=timezone.now() - timedelta(days=29))
        self.assertContains(self.client.get("/items/matched/"), "Undo matching glove")
        ItemAlias.objects.update(created_at=timezone.now() - timedelta(days=31))
        self.assertNotContains(self.client.get("/items/matched/"), "glove &rarr;")
        self.assertEqual(self.client.post(f"/items/matched/{alias.pk}/undo/").status_code, 403)

    def test_import_skips_a_row_already_on_the_list(self):
        self.client.force_login(self.admin)
        csv = SimpleUploadedFile("items.csv", b"name,unit,supplier,price,order_size,count\ngloves ,box,Henry Schein,,,\n")
        response = self.client.post("/items/import/preview/", {"csv_file": csv})
        self.assertContains(response, "Already have: Gloves")
        items_before = Item.objects.count()
        self.client.post("/items/import/confirm/")
        self.assertEqual(Item.objects.count(), items_before)

    def test_import_asks_about_a_likely_match(self):
        self.client.force_login(self.admin)
        rows = b"name,unit,supplier,price,order_size,count\nPolish strips,box,Henry Schein,,,\n"

        def preview_and_confirm(same):
            csv = SimpleUploadedFile("items.csv", rows)
            self.assertContains(self.client.post("/items/import/preview/", {"csv_file": csv}),
                                "Likely match – 89%: is it Polishing strips?")
            self.client.post("/items/import/confirm/", {"same_0": same})

        preview_and_confirm(same="")  # left as a new item
        new = Item.objects.get(name="Polish strips")
        self.assertFalse(ItemAlias.objects.exists())

        new.is_active = False
        new.save()
        preview_and_confirm(same="1")
        self.assertEqual(Item.objects.filter(name="Polish strips").count(), 1)
        alias = ItemAlias.objects.get()
        self.assertEqual((alias.item, alias.method, alias.source), (self.strips, "fuzzy", "import"))

    def test_the_catalogue_hides_a_product_stocked_under_another_spelling(self):
        self.item("Nitrile glove, size M")
        self.client.force_login(self.admin)
        response = self.client.get("/items/catalogue/")
        self.assertNotContains(response, "Nitrile gloves, size M")
        self.assertContains(response, "Nitrile gloves, size L")
