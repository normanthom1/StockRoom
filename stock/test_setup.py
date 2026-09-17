from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Organisation, User

from . import setup
from .models import Invoice, InvoiceLine, Item, StockEvent, Supplier


def invoice(org, supplier_name="Henry Schein", issued_on=date(2026, 8, 12), status=Invoice.Status.CONFLICT):
    """An invoice as a batch import leaves it for a practice with no suppliers
    yet: read fine, but nothing to match it to, so it needs checking."""
    return Invoice.objects.create(organisation=org, supplier_name=supplier_name, issued_on=issued_on, status=status)


def line(inv, description, qty=None, unit_price=None, sku=""):
    return InvoiceLine.objects.create(organisation=inv.organisation, invoice=inv, description=description,
                                      sku=sku, qty=qty, unit_price=unit_price)


class DraftItemsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")

    def test_groups_the_same_product_across_invoices(self):
        for day, qty, price in [(1, 4, "8.50"), (2, 6, "8.75"), (3, 5, "9.00")]:
            line(invoice(self.org, issued_on=date(2026, 6, day)), "Nitrile gloves, size M", qty, Decimal(price))

        drafts = setup.draft_items(self.org)

        self.assertEqual(len(drafts), 1)
        draft = drafts[0]
        self.assertEqual(draft.times, 3)
        self.assertEqual(draft.total_qty, 15)
        # The newest invoice is what they pay now and what last arrived.
        self.assertEqual(draft.price, Decimal("9.00"))
        self.assertEqual(draft.on_hand, 5)
        self.assertEqual(draft.order_size, 5)  # the median of 4, 6, 5
        self.assertEqual(draft.supplier_name, "Henry Schein")
        self.assertEqual(draft.last_seen, date(2026, 6, 3))

    def test_matches_the_same_product_written_differently(self):
        """The name matching that stops invoices making duplicate items has to
        stop the draft making them too."""
        line(invoice(self.org), "Nitrile Gloves Size M")
        line(invoice(self.org, issued_on=date(2026, 8, 20)), "gloves, nitrile, size m")

        drafts = setup.draft_items(self.org)

        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].times, 2)

    def test_leaves_out_what_the_practice_already_stocks(self):
        supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
        Item.objects.create(organisation=self.org, name="Nitrile gloves, size M", unit="box", supplier=supplier)
        line(invoice(self.org), "Nitrile gloves size M")
        line(invoice(self.org), "Patient bibs")

        self.assertEqual([d.name for d in setup.draft_items(self.org)], ["Patient bibs"])

    def test_leaves_out_lines_already_matched_and_repeat_invoices(self):
        supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
        item = Item.objects.create(organisation=self.org, name="Bibs", unit="pack", supplier=supplier)
        matched = line(invoice(self.org), "Patient bibs")
        matched.item = item
        matched.save()
        line(invoice(self.org, status=Invoice.Status.IGNORED), "Suction tips")

        self.assertEqual(setup.draft_items(self.org), [])

    def test_never_sees_another_practices_invoices(self):
        other = Organisation.objects.create(name="Other Dental")
        line(invoice(other), "Nitrile gloves, size M")

        self.assertEqual(setup.draft_items(self.org), [])

    def test_commonest_first(self):
        for _ in range(3):
            line(invoice(self.org), "Nitrile gloves, size M")
        line(invoice(self.org), "Rubber dam clamps")

        self.assertEqual([d.name for d in setup.draft_items(self.org)],
                         ["Nitrile gloves, size M", "Rubber dam clamps"])

    def test_guesses_the_unit_from_the_description(self):
        self.assertEqual(setup.guess_unit("Nitrile gloves 100/box"), "box")
        self.assertEqual(setup.guess_unit("Lignospan cartridges x50"), "cartridge")
        self.assertEqual(setup.guess_unit("Etchant syringes"), "syringe")
        self.assertEqual(setup.guess_unit("Something unfamiliar"), setup.DEFAULT_UNIT)


class CreateDraftsTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.user = User.objects.create_user("manager@test.test", "pw", organisation=self.org, role=User.Role.ADMIN)

    def test_creates_the_supplier_item_and_first_count(self):
        line(invoice(self.org), "Nitrile gloves, size M", qty=4, unit_price=Decimal("8.50"), sku="HS-GLV-M")

        items = setup.create_drafts(self.org, self.user, setup.draft_items(self.org))

        item = items[0]
        self.assertEqual(item.name, "Nitrile gloves, size M")
        self.assertEqual(item.price, Decimal("8.50"))
        self.assertEqual(item.supplier.name, "Henry Schein")
        self.assertEqual(item.supplier_sku, "HS-GLV-M")
        count = StockEvent.objects.get(item=item)
        self.assertEqual((count.kind, count.qty, count.user), ("count", 4, self.user))

    def test_reuses_a_supplier_the_practice_already_has(self):
        existing = Supplier.objects.create(organisation=self.org, name="henry schein")
        line(invoice(self.org), "Nitrile gloves, size M", qty=4)

        items = setup.create_drafts(self.org, self.user, setup.draft_items(self.org))

        self.assertEqual(items[0].supplier, existing)
        self.assertEqual(Supplier.objects.for_org(self.org).count(), 1)

    def test_the_lines_leave_the_draft_without_being_received(self):
        """The count is what's on the shelf today. Receiving a year of old
        deliveries on top of it would double the stock."""
        inv = invoice(self.org)
        line(inv, "Nitrile gloves, size M", qty=4)

        setup.create_drafts(self.org, self.user, setup.draft_items(self.org))

        self.assertEqual(setup.draft_items(self.org), [])
        self.assertFalse(inv.lines.get().is_received)
        self.assertEqual(StockEvent.objects.filter(kind="received").count(), 0)

    def test_no_count_when_the_invoice_had_no_quantity(self):
        line(invoice(self.org), "Nitrile gloves, size M")

        setup.create_drafts(self.org, self.user, setup.draft_items(self.org))

        self.assertEqual(StockEvent.objects.count(), 0)


class SetupPageTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.user = User.objects.create_user("manager@test.test", "pw", organisation=self.org, role=User.Role.ADMIN)
        self.client.force_login(self.user)

    def test_signup_lands_on_setup(self):
        self.client.logout()
        with self.settings(SIGNUP_ENABLED=True):
            response = self.client.post(reverse("signup"), {
                "practice_name": "New Dental", "email": "new@dental.test", "password": "a-long-enough-password",
                "name": "Sam", "pin": "4321",
            })
        self.assertRedirects(response, reverse("stock:setup"), fetch_redirect_response=False)

    def test_checklist_ticks_itself_off_from_the_practices_own_data(self):
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get(reverse("stock:setup"))
            self.assertEqual(response.context["done_count"], 0)

            supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
            item = Item.objects.create(organisation=self.org, name="Gloves", unit="box", supplier=supplier)
            StockEvent.objects.create(organisation=self.org, item=item, user=self.user, kind="count", qty=3)

            response = self.client.get(reverse("stock:setup"))
            self.assertEqual(response.context["done_count"], 3)  # all but the team step

    def test_finishing_every_step_puts_the_checklist_away(self):
        supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
        item = Item.objects.create(organisation=self.org, name="Gloves", unit="box", supplier=supplier)
        StockEvent.objects.create(organisation=self.org, item=item, user=self.user, kind="count", qty=3)
        User.objects.create_staff(self.org, "Jo", "11")

        self.client.get(reverse("stock:setup"))

        self.org.refresh_from_db()
        self.assertIsNotNone(self.org.setup_dismissed_at)

    def test_every_link_on_the_checklist_goes_somewhere_without_an_ai_key(self):
        """UX-01. The checklist used to point step 1 at /invoices/, which is
        @ai_required and so 404s without a key: a brand-new practice tapped the
        one big blue button and got an error page."""
        with self.settings(AI_API_KEY=""):
            response = self.client.get(reverse("stock:setup"))
            for step in response.context["steps"]:
                # follow=True so a step that redirects (stocktake with nothing
                # to count) still has to land on a real page.
                landed = self.client.get(reverse(step["url"]), follow=True)
                self.assertEqual(landed.status_code, 200, f"{step['title']} -> {step['url']}")

    def test_without_an_ai_key_the_checklist_offers_the_catalogue_not_invoices(self):
        with self.settings(AI_API_KEY=""):
            response = self.client.get(reverse("stock:setup"))

        steps = response.context["steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual(response.context["next_step"]["url"], "stock:catalogue")
        wording = " ".join(f"{s['title']} {s['blurb']} {s['cta']}" for s in steps).lower()
        self.assertNotIn("invoice", wording)

    def test_with_an_ai_key_the_checklist_still_starts_with_invoices(self):
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get(reverse("stock:setup"))

        self.assertEqual(len(response.context["steps"]), 4)
        self.assertEqual(response.context["next_step"]["url"], "stock:invoice_upload")

    def test_the_empty_draft_page_does_not_offer_an_upload_without_an_ai_key(self):
        with self.settings(AI_API_KEY=""):
            response = self.client.get(reverse("stock:setup_draft"))

        self.assertNotContains(response, reverse("stock:invoice_upload"))
        self.assertContains(response, reverse("stock:catalogue"))

    def test_skip_puts_it_away_and_leaves_everything_else_alone(self):
        response = self.client.post(reverse("stock:setup_skip"))

        self.assertRedirects(response, reverse("stock:home"), fetch_redirect_response=False)
        self.org.refresh_from_db()
        self.assertIsNotNone(self.org.setup_dismissed_at)
        self.assertEqual(Item.objects.for_org(self.org).count(), 0)


class DraftReviewTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.user = User.objects.create_user("manager@test.test", "pw", organisation=self.org, role=User.Role.ADMIN)
        self.client.force_login(self.user)
        line(invoice(self.org), "GLOVE NITRILE MED BX100", qty=4, unit_price=Decimal("8.50"))

    def post(self, **overrides):
        data = {
            "key_0": setup.draft_items(self.org)[0].key,
            "pick_0": "1",
            "name_0": "Nitrile gloves, size M",
            "supplier_0": "Henry Schein",
            "unit_0": "box",
            "price_0": "9.25",
            "order_size_0": "6",
            "on_hand_0": "2",
        }
        data.update(overrides)
        return self.client.post(reverse("stock:setup_draft_confirm"), data)

    def test_the_review_screen_shows_what_the_invoices_said(self):
        response = self.client.get(reverse("stock:setup_draft"))

        draft = response.context["drafts"][0]
        self.assertEqual(draft.name, "GLOVE NITRILE MED BX100")
        self.assertEqual(draft.on_hand, 4)

    def test_confirming_saves_the_managers_edits_not_the_invoices_guesses(self):
        self.post()

        item = Item.objects.for_org(self.org).get()
        self.assertEqual(item.name, "Nitrile gloves, size M")
        self.assertEqual(item.price, Decimal("9.25"))
        self.assertEqual(item.order_size, 6)
        self.assertEqual(StockEvent.objects.get(item=item).qty, 2)

    def test_an_unticked_draft_is_not_added(self):
        self.post(pick_0="")

        self.assertEqual(Item.objects.for_org(self.org).count(), 0)

    def test_a_draft_with_no_supplier_is_left_rather_than_half_made(self):
        self.post(supplier_0="  ")

        self.assertEqual(Item.objects.for_org(self.org).count(), 0)
        self.assertEqual(Supplier.objects.for_org(self.org).count(), 0)

    def test_another_practice_cannot_claim_a_draft_by_posting_its_key(self):
        other = Organisation.objects.create(name="Other Dental")
        line(invoice(other, supplier_name="Aluro"), "Rubber dam clamps", qty=2)
        their_key = setup.draft_items(other)[0].key

        self.post(key_0=their_key)

        self.assertEqual(Item.objects.for_org(self.org).count(), 0)
        self.assertEqual(Item.objects.for_org(other).count(), 0)
