from django.test import TestCase

from accounts.models import Organisation, User

from .catalogue_data import ALURO, DE_HEALTHCARE, DENTSPLY, HENRY_SCHEIN, INDEPENDENT
from .models import CatalogueProduct, Item, OrderLine, StockEvent, Supplier


def product(name):
    return CatalogueProduct.objects.get(name=name)


def sellers(name):
    return [offer.supplier.name for offer in product(name).offers.all()]


class CatalogueDataTests(TestCase):
    """The catalogue arrives with the migrations, suppliers matched by the ranges they sell."""

    def test_the_catalogue_is_loaded(self):
        self.assertGreater(CatalogueProduct.objects.count(), 100)
        self.assertEqual(product("Articaine 4% w/ adrenaline").category, "Anaesthetic")

    def test_suppliers_only_sell_what_their_range_covers(self):
        # DE Healthcare does infection control and disposables, not cartridges or composite.
        self.assertIn(DE_HEALTHCARE, sellers("Nitrile gloves, size L"))
        self.assertNotIn(DE_HEALTHCARE, sellers("Articaine 4% w/ adrenaline"))
        self.assertNotIn(DE_HEALTHCARE, sellers("Composite, A3 syringes"))
        # Dentsply for its own lines only.
        self.assertIn(DENTSPLY, sellers("Rotary files"))
        self.assertNotIn(DENTSPLY, sellers("Nitrile gloves, size L"))

    def test_every_product_has_at_least_two_suppliers_the_first_being_the_default(self):
        for p in CatalogueProduct.objects.prefetch_related("offers__supplier"):
            with self.subTest(p.name):
                self.assertGreaterEqual(p.offers.count(), 2)
        self.assertEqual(sellers("Saliva ejectors")[:2], [HENRY_SCHEIN, INDEPENDENT])


class Practice(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", name="Sandy", organisation=cls.org,
                                             role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("jo@example.com", "pw", organisation=cls.org)
        cls.henry = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.independent = Supplier.objects.create(organisation=cls.org, name="independent dental supplies", lead_days=3)

    def setUp(self):
        self.client.force_login(self.admin)

    def add(self, name, supplier):
        return self.client.post(f"/items/catalogue/{product(name).pk}/add/", {"supplier": supplier}, follow=True)


class CatalogueTests(Practice):
    def test_what_the_practice_already_stocks_isnt_offered(self):
        Item.objects.create(organisation=self.org, name="SALIVA EJECTORS", unit="ejector", supplier=self.henry)
        content = self.client.get("/items/catalogue/").content.decode()
        self.assertNotIn("Saliva ejectors", content)
        self.assertIn("Cotton rolls", content)

    def test_the_sheet_starts_on_a_supplier_the_practice_uses(self):
        # Rotary files: Henry Schein, Dentsply, Independent, Aluro. The practice uses Henry Schein and Independent.
        content = self.client.get(f"/items/catalogue/{product('Rotary files').pk}/").content.decode()
        self.assertRegex(content, r'value="Henry Schein" checked')
        self.assertIn("You use them", content)

    def test_adding_uses_the_chosen_supplier_and_keeps_the_others_you_use_as_backups(self):
        response = self.add("Saliva ejectors", INDEPENDENT)
        item = Item.objects.get(name="Saliva ejectors")
        self.assertEqual((item.unit, item.supplier), ("ejector", self.independent))
        self.assertEqual(list(item.other_suppliers.all()), [self.henry])
        # The catalogue's other sellers the practice doesn't deal with aren't created.
        self.assertFalse(Supplier.objects.filter(name=DE_HEALTHCARE).exists())
        self.assertContains(response, "Added Saliva ejectors.")

    def test_choosing_a_supplier_you_dont_use_yet_adds_them(self):
        response = self.add("Articaine 4% w/ adrenaline", ALURO)
        aluro = Supplier.objects.get(organisation=self.org, name=ALURO)
        self.assertEqual(Item.objects.get(name="Articaine 4% w/ adrenaline").supplier, aluro)
        self.assertContains(response, "set how long their deliveries take on the Suppliers page")

    def test_an_archived_supplier_comes_back_rather_than_clashing(self):
        self.henry.is_active = False
        self.henry.save()
        self.add("Cotton rolls", HENRY_SCHEIN)
        self.henry.refresh_from_db()
        self.assertTrue(self.henry.is_active)
        self.assertEqual(Item.objects.get(name="Cotton rolls").supplier, self.henry)

    def test_only_a_supplier_that_sells_it(self):
        self.add("Articaine 4% w/ adrenaline", DE_HEALTHCARE)
        self.assertFalse(Item.objects.filter(name="Articaine 4% w/ adrenaline").exists())

    def test_undo_removes_an_item_nothing_has_happened_to(self):
        self.add("Cotton rolls", HENRY_SCHEIN)
        item = Item.objects.get(name="Cotton rolls")
        response = self.client.post(f"/items/catalogue/undo/{item.pk}/", HTTP_HX_REQUEST="true")
        self.assertEqual(response["HX-Redirect"], "/items/catalogue/")
        self.assertFalse(Item.objects.filter(pk=item.pk).exists())

    def test_undo_refuses_once_the_item_has_history(self):
        self.add("Cotton rolls", HENRY_SCHEIN)
        item = Item.objects.get(name="Cotton rolls")
        StockEvent.objects.create(organisation=self.org, item=item, user=self.admin, kind="count", qty=5)
        self.assertEqual(self.client.post(f"/items/catalogue/undo/{item.pk}/").status_code, 403)

    def test_managers_only(self):
        self.client.force_login(self.assistant)
        self.assertEqual(self.client.get("/items/catalogue/").status_code, 403)


class SwitchSupplierTests(Practice):
    def setUp(self):
        super().setUp()
        self.gloves = Item.objects.create(organisation=self.org, name="Gloves", unit="box", supplier=self.henry)
        self.gloves.other_suppliers.add(self.independent)
        StockEvent.objects.create(organisation=self.org, item=self.gloves, user=self.admin, kind="count", qty=0)

    def switch(self, to, **extra):
        return self.client.post(f"/item/{self.gloves.pk}/supplier/?to={to.pk}", **extra)

    def test_the_reorder_list_offers_the_backup(self):
        content = self.client.get("/reorder/").content.decode()
        self.assertIn('Also from independent dental supplies, <span class="whitespace-nowrap">~3 days</span>', content)
        self.assertIn(f"/item/{self.gloves.pk}/supplier/?to={self.independent.pk}", content)

    def test_switching_swaps_preferred_and_backup(self):
        self.assertRedirects(self.switch(self.independent), "/reorder/", fetch_redirect_response=False)
        self.gloves.refresh_from_db()
        self.assertEqual(self.gloves.supplier, self.independent)
        self.assertEqual(list(self.gloves.other_suppliers.all()), [self.henry])

    def test_undo_switches_back(self):
        self.switch(self.independent)
        response = self.client.post(f"/item/{self.gloves.pk}/supplier/?to={self.henry.pk}&next=/reorder/",
                                    HTTP_HX_REQUEST="true")
        self.assertEqual(response["HX-Redirect"], "/reorder/")
        self.gloves.refresh_from_db()
        self.assertEqual(self.gloves.supplier, self.henry)

    def test_only_to_one_of_its_active_backups(self):
        stranger = Supplier.objects.create(organisation=self.org, name="Someone Else")
        self.assertEqual(self.switch(stranger).status_code, 404)
        self.independent.is_active = False
        self.independent.save()
        self.assertEqual(self.switch(self.independent).status_code, 404)
        self.assertEqual(self.client.post(f"/item/{self.gloves.pk}/supplier/?to=abc").status_code, 404)

    def test_next_must_be_this_site(self):
        response = self.client.post(f"/item/{self.gloves.pk}/supplier/?to={self.independent.pk}&next=https://evil.example/")
        self.assertRedirects(response, "/reorder/", fetch_redirect_response=False)

    def test_orders_stay_with_the_supplier_they_went_to(self):
        self.client.post(f"/reorder/item/{self.gloves.pk}/ordered/", {f"qty_{self.gloves.pk}": "10"})
        order = OrderLine.objects.get(item=self.gloves)
        self.assertEqual(order.supplier, self.henry)

        self.switch(self.independent)
        deliveries = self.client.get("/deliveries/").content.decode()
        self.assertIn(f"/deliveries/supplier/{self.henry.pk}/", deliveries)  # still waiting on Henry Schein
        self.assertNotIn(f"/deliveries/supplier/{self.independent.pk}/", deliveries)

        self.client.post(f"/deliveries/supplier/{self.henry.pk}/", {"receive_line": order.pk, f"qty_{order.pk}": "4"})
        remainder = OrderLine.objects.get(item=self.gloves, received_at__isnull=True)
        self.assertEqual((remainder.qty, remainder.supplier), (6, self.henry))

    def test_editing_an_item_to_its_backup_drops_it_from_the_backups(self):
        self.client.post(f"/items/{self.gloves.pk}/update/",
                         {"name": "Gloves", "unit": "box", "supplier": self.independent.pk})
        self.gloves.refresh_from_db()
        self.assertEqual(self.gloves.supplier, self.independent)
        self.assertEqual(list(self.gloves.other_suppliers.all()), [])

    def test_adding_and_removing_backups_on_the_item_page(self):
        dentsply = Supplier.objects.create(organisation=self.org, name="Dentsply")
        content = self.client.get(f"/item/{self.gloves.pk}/").content.decode()
        self.assertIn("Make preferred", content)
        self.assertIn(f'<option value="{dentsply.pk}">Dentsply</option>', content)

        self.client.post(f"/item/{self.gloves.pk}/other-suppliers/", {"add": dentsply.pk})
        self.client.post(f"/item/{self.gloves.pk}/other-suppliers/", {"remove": self.independent.pk})
        self.assertEqual(list(self.gloves.other_suppliers.all()), [dentsply])

    def test_backups_come_from_this_practice_only(self):
        elsewhere = Supplier.objects.create(organisation=Organisation.objects.create(name="Other"), name="Theirs")
        response = self.client.post(f"/item/{self.gloves.pk}/other-suppliers/", {"add": elsewhere.pk})
        self.assertEqual(response.status_code, 404)
