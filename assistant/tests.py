import io
import json
import urllib.error
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organisation, User
from accounts.ratelimit import AI_PER_HOUR
from assistant.views import MAX_BATCH
from stock.forecast import on_hand
from stock.invoices import run_batch
from stock.models import (
    DemoResetState,
    Invoice,
    InvoiceBatch,
    InvoiceLine,
    Item,
    OrderLine,
    StockEvent,
    Supplier,
)

from . import gemini
from .views import HISTORY_TURNS

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def answer(text="Gloves are running low."):
    return mock.patch("assistant.gemini.generate", return_value=text)


class Practice(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", name="Sandy", organisation=cls.org,
                                             role=User.Role.ADMIN)
        cls.assistant = User.objects.create_user("jo@example.com", "pw", name="Johanna", organisation=cls.org)
        cls.henry = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.gloves = Item.objects.create(organisation=cls.org, name="Gloves", unit="box", supplier=cls.henry, price="8.50")
        StockEvent.objects.create(organisation=cls.org, item=cls.gloves, user=cls.admin, kind="count", qty=3)
        OrderLine.objects.create(organisation=cls.org, item=cls.gloves, qty=10, unit_price="8.50", ordered_by=cls.admin)

    def ask(self, question="What's running low?"):
        return self.client.post("/ask/", {"q": question})


@override_settings(AI_API_KEY="")
class NoKeyTests(Practice):
    def test_everything_ai_is_hidden_without_a_key(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/ask/").status_code, 404)
        self.assertEqual(self.client.post("/items/import/ai/", {"text": "gloves"}).status_code, 404)
        self.assertEqual(self.client.get("/invoices/").status_code, 404)
        self.assertEqual(self.client.post("/invoices/upload/", {}).status_code, 404)
        self.assertNotContains(self.client.get("/"), 'href="/ask/"')
        self.assertContains(self.client.get("/items/import/"), "Import items from a CSV")


@override_settings(AI_API_KEY="test-key")
class AskTests(Practice):
    def test_the_header_links_to_ask(self):
        self.client.force_login(self.assistant)
        self.assertContains(self.client.get("/"), 'href="/ask/"')

    def test_suggestions_match_the_role(self):
        self.client.force_login(self.assistant)
        content = self.client.get("/ask/").content.decode()
        self.assertIn("What&#x27;s running low?", content)
        self.assertNotIn("What did we spend", content)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/ask/"), "What did we spend this month?")

    def test_the_answer_is_added_to_the_chat_as_plain_text(self):
        self.client.force_login(self.assistant)
        with answer("<b>Gloves</b> are **low**.\nOrder soon.") as generate:
            response = self.ask()
        self.assertContains(response, "What&#x27;s running low?")
        self.assertContains(response, "&lt;b&gt;Gloves&lt;/b&gt; are low.<br>Order soon.")
        self.assertNotContains(response, "<html")  # just the new turn, for htmx to append
        self.assertEqual(generate.call_args.args[1], [("user", "What's running low?")])

    def test_an_assistants_prompt_has_no_prices_or_spending(self):
        self.client.force_login(self.assistant)
        with answer() as generate:
            self.ask()
        system = generate.call_args.args[0]
        self.assertIn("Gloves", system)
        self.assertIn("on hand 3 boxes", system)
        self.assertNotIn("$", system)
        self.assertNotIn("spent", system)
        self.assertIn("talking to Johanna, a dental assistant", system)

    def test_a_managers_prompt_has_prices_and_spending(self):
        self.client.force_login(self.admin)
        with answer() as generate:
            self.ask()
        system = generate.call_args.args[0]
        self.assertIn("$8.50 per box", system)
        self.assertIn("spent $85.00 this month", system)
        self.assertIn("This month", system)

    def test_a_managers_prompt_has_invoice_and_back_order_data(self):
        self.client.force_login(self.admin)
        invoice = Invoice.objects.create(
            organisation=self.org, supplier=self.henry, issued_on="2024-09-10",
            invoice_number="INV-42", status=Invoice.Status.COMPLETE,
        )
        InvoiceLine.objects.create(organisation=self.org, invoice=invoice, item=self.gloves, qty=10, unit_price="8.50")
        with answer() as generate:
            self.ask()
        system = generate.call_args.args[0]
        self.assertIn("10 Sep 2024: Henry Schein, received", system)
        self.assertIn("invoice number INV-42", system)
        self.assertIn("10 Gloves at $8.50 each", system)
        self.assertIn("Still on order or back-order:", system)
        self.assertIn("10 Gloves from Henry Schein, ordered", system)

    def test_an_assistants_prompt_has_no_invoice_or_back_order_data(self):
        self.client.force_login(self.assistant)
        invoice = Invoice.objects.create(
            organisation=self.org, supplier=self.henry, issued_on="2024-09-10", status=Invoice.Status.COMPLETE,
        )
        InvoiceLine.objects.create(organisation=self.org, invoice=invoice, item=self.gloves, qty=10, unit_price="8.50")
        with answer() as generate:
            self.ask()
        system = generate.call_args.args[0]
        self.assertNotIn("Invoices", system)
        self.assertNotIn("Still on order", system)
        self.assertNotIn("$", system)

    def test_earlier_questions_go_back_with_each_new_one_up_to_a_limit(self):
        self.client.force_login(self.admin)
        with answer("An answer.") as generate:
            for i in range(HISTORY_TURNS + 2):
                self.ask(f"Question {i}")
        turns = generate.call_args.args[1]
        self.assertEqual(len(turns), HISTORY_TURNS * 2 + 1)
        self.assertEqual(turns[0], ("user", "Question 1"))
        self.assertEqual(turns[-1], ("user", f"Question {HISTORY_TURNS + 1}"))
        self.assertContains(self.client.get("/ask/"), "Question 7")

    def test_switching_person_starts_a_fresh_chat(self):
        self.client.force_login(self.admin)
        with answer("We spent $85."):
            self.ask("What did we spend?")
        self.client.force_login(self.assistant)  # what entering a code does (accounts.middleware.switch_to)
        self.assertNotContains(self.client.get("/ask/"), "We spent")

    def test_a_failed_answer_says_so_and_isnt_remembered(self):
        self.client.force_login(self.admin)
        with mock.patch("assistant.gemini.generate", side_effect=gemini.GeminiError):
            response = self.ask()
        self.assertContains(response, "couldn&#x27;t get an answer")
        self.assertEqual(self.client.session.get("ask_history", []), [])

    def test_an_empty_question_does_nothing(self):
        self.client.force_login(self.admin)
        with answer() as generate:
            self.assertEqual(self.ask("  ").status_code, 204)
        generate.assert_not_called()


@override_settings(AI_API_KEY="test-key", CACHES=LOCMEM)
class AskLimitTests(Practice):
    def setUp(self):
        cache.clear()

    def test_each_person_gets_a_limited_number_an_hour(self):
        self.client.force_login(self.admin)
        with answer() as generate:
            for _ in range(AI_PER_HOUR):
                self.ask()
            response = self.ask()
            self.assertEqual(generate.call_count, AI_PER_HOUR)
        self.assertContains(response, "the limit for now")
        self.client.force_login(self.assistant)
        with answer() as generate:
            self.ask()
        generate.assert_called_once()

    @override_settings(AI_DAILY_LIMIT=3)
    def test_every_practice_together_has_a_daily_cap(self):
        with answer() as generate:
            for user in (self.admin, self.assistant, self.admin, self.assistant):
                self.client.force_login(user)
                self.ask()
        self.assertEqual(generate.call_count, 3)

    @override_settings(DEMO_MODE=True)
    def test_on_the_demo_the_limit_is_per_device(self):
        # Every demo visitor signs in as the same staff, so a person isn't a visitor.
        DemoResetState.objects.create(pk=1, date=timezone.localdate())  # no reseed mid-test
        with answer() as generate:
            for i in range(AI_PER_HOUR + 1):
                self.client.force_login(self.admin if i % 2 else self.assistant)
                self.client.post("/ask/", {"q": "Hi"}, REMOTE_ADDR="203.0.113.9")
            self.assertEqual(generate.call_count, AI_PER_HOUR)
            self.client.post("/ask/", {"q": "Hi"}, REMOTE_ADDR="203.0.113.10")
            self.assertEqual(generate.call_count, AI_PER_HOUR + 1)


@override_settings(AI_API_KEY="test-key")
class ImportWithAITests(Practice):
    ROWS = [
        {"name": "Nitrile gloves, size L", "unit": "box", "supplier": "Henry Schein", "price": 8.5, "order_size": 10},
        {"name": "Bib clips", "unit": "clip", "supplier": "Nobody Ltd", "price": None, "count": 4},
    ]

    def test_the_rows_go_through_the_normal_preview_and_nothing_is_saved_yet(self):
        self.client.force_login(self.admin)
        with answer(self.ROWS) as generate:
            response = self.client.post("/items/import/ai/", {"text": "gloves L x10 $8.50 Henry\nbib clips"})
        self.assertContains(response, "1 item ready to import")
        self.assertContains(response, "Nitrile gloves, size L")
        self.assertContains(response, "$8.50")  # Gemini's 8.5, shown as money
        self.assertContains(response, "No supplier named &quot;Nobody Ltd&quot;")
        self.assertFalse(Item.objects.filter(name="Nitrile gloves, size L").exists())
        self.assertIn("Henry Schein", generate.call_args.args[0])  # its suppliers, to match against
        self.assertIsNotNone(generate.call_args.kwargs["schema"])

        self.client.post("/items/import/confirm/")
        item = Item.objects.get(name="Nitrile gloves, size L")
        self.assertEqual((item.unit, item.supplier, str(item.price), item.order_size), ("box", self.henry, "8.50", 10))

    def test_a_photo_is_sent_along(self):
        self.client.force_login(self.admin)
        photo = SimpleUploadedFile("invoice.jpg", b"jpeg bytes", content_type="image/jpeg")
        with answer(self.ROWS[:1]) as generate:
            self.client.post("/items/import/ai/", {"document": photo})
        self.assertEqual(generate.call_args.kwargs["attachment"], ("image/jpeg", b"jpeg bytes"))

    def test_only_photos_and_pdfs(self):
        self.client.force_login(self.admin)
        with answer() as generate:
            response = self.client.post("/items/import/ai/", {"document": SimpleUploadedFile("a.txt", b"x", "text/plain")})
        self.assertRedirects(response, "/items/import/")
        generate.assert_not_called()

    def test_nothing_found_or_a_failure_goes_back_with_a_message(self):
        self.client.force_login(self.admin)
        with answer([]):
            response = self.client.post("/items/import/ai/", {"text": "hello"}, follow=True)
        self.assertContains(response, "Couldn&#x27;t find any items")
        with mock.patch("assistant.gemini.generate", side_effect=gemini.GeminiError):
            response = self.client.post("/items/import/ai/", {"text": "gloves"}, follow=True)
        self.assertContains(response, "import a CSV instead")

    def test_managers_only(self):
        self.client.force_login(self.assistant)
        self.assertEqual(self.client.post("/items/import/ai/", {"text": "gloves"}).status_code, 403)


@override_settings(AI_API_KEY="test-key")
class InvoiceUploadTests(Practice):
    CSV = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,2,8.50\n"

    def upload(self, content=CSV, content_type="text/csv", name="invoice.csv"):
        return self.client.post("/invoices/upload/", {"invoice_file": SimpleUploadedFile(name, content, content_type)})

    def test_a_csv_is_read_without_gemini_and_shows_a_preview(self):
        self.client.force_login(self.admin)
        with mock.patch("assistant.gemini.generate") as generate:
            response = self.upload()
        generate.assert_not_called()
        self.assertContains(response, "Gloves")
        self.assertContains(response, "1 matched")
        self.assertContains(response, "On order: Gloves &middot; 10 boxes")
        self.assertFalse(Invoice.objects.exists())  # nothing written until confirmed

    def test_confirming_creates_the_invoice_and_its_lines(self):
        self.client.force_login(self.admin)
        self.upload()
        response = self.client.post("/invoices/confirm/", {"qty_0": "2", "price_0": "8.50"})
        invoice = Invoice.objects.get()
        self.assertRedirects(response, f"/invoices/{invoice.pk}/")
        self.assertEqual(invoice.supplier, self.henry)
        self.assertTrue(invoice.totals_ok)
        line = invoice.lines.get()
        self.assertEqual((line.qty, str(line.unit_price), line.item), (2, "8.50", self.gloves))

    def test_a_price_rise_over_10_percent_needs_a_tick_before_it_applies(self):
        self.client.force_login(self.admin)
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,10,10.00\n"
        response = self.upload(content=csv)
        self.assertContains(response, "Gloves went from $8.50 to $10.00 - up 18%")

        self.client.post("/invoices/confirm/", {"qty_0": "10", "price_0": "10.00"})
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "8.50")  # unticked: the price is left alone
        order = OrderLine.objects.get(item=self.gloves)
        self.assertEqual(str(order.unit_price), "10.00")  # still recorded, for spending reports

    def test_ticking_a_price_rise_applies_it_and_logs_the_old_value(self):
        self.client.force_login(self.admin)
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,10,10.00\n"
        self.upload(content=csv)
        self.client.post("/invoices/confirm/", {"qty_0": "10", "price_0": "10.00", "price_confirm_0": "1"})
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "10.00")

        activity = self.client.get("/activity/")
        self.assertContains(activity, "Price updated from $8.50 to $10.00")
        self.assertContains(activity, "Henry Schein")

    def test_a_modest_price_change_applies_without_a_tick(self):
        self.client.force_login(self.admin)
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,10,9.00\n"
        response = self.upload(content=csv)
        self.assertNotContains(response, "Tick to update")
        self.client.post("/invoices/confirm/", {"qty_0": "10", "price_0": "9.00"})
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "9.00")

    def test_no_price_or_price_change_is_shown_to_an_assistant(self):
        self.client.force_login(self.assistant)
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,10,10.00\n"
        self.assertEqual(self.upload(content=csv).status_code, 403)
        self.assertEqual(self.client.get("/invoices/").status_code, 403)
        self.assertEqual(self.client.get("/activity/").status_code, 403)

    def test_editing_a_line_before_confirming_changes_what_is_saved(self):
        self.client.force_login(self.admin)
        self.upload()
        self.client.post("/invoices/confirm/", {"qty_0": "3", "price_0": "9.00"})
        invoice = Invoice.objects.get()
        line = invoice.lines.get()
        self.assertEqual((line.qty, str(line.unit_price), invoice.totals_ok), (3, "9.00", True))

    def test_one_confirm_receives_everything_on_order_and_importing_it_again_adds_nothing(self):
        self.client.force_login(self.admin)
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,HS-GLV,Gloves,10,8.50\n"
        self.assertContains(self.client.get("/"), "On order")
        self.upload(content=csv)
        response = self.client.post("/invoices/confirm/", {}, follow=True)
        self.assertContains(response, "Received everything on the invoice.")
        self.assertContains(response, "1 received")
        self.assertContains(self.client.get("/deliveries/"), "Nothing on order right now.")
        self.assertNotContains(self.client.get("/"), "On order")
        self.assertNotContains(self.client.get("/reorder/"), "Order quantity for Gloves")
        self.assertEqual(on_hand(self.gloves.events.all()), 13)
        self.gloves.refresh_from_db()
        self.assertEqual(self.gloves.supplier_sku, "HS-GLV")  # so next month's invoice matches by code

        self.upload(content=csv, name="invoice (1).csv")
        self.assertEqual(on_hand(self.gloves.events.all()), 13)

    def test_undo_reverses_the_stock_order_status_and_price_a_confirm_changed(self):
        self.client.force_login(self.admin)
        before = on_hand(self.gloves.events.all())
        csv = b"vendor,sku,description,qty,unit\nHenry Schein,HS-GLV,Gloves,10,10.00\n"
        self.upload(content=csv)
        response = self.client.post("/invoices/confirm/", {"price_confirm_0": "1"}, follow=True)
        invoice = Invoice.objects.get()
        order = OrderLine.objects.get(item=self.gloves)
        self.assertEqual(on_hand(self.gloves.events.all()), before + 10)
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "10.00")
        self.assertIsNotNone(order.received_at)
        self.assertContains(response, f'hx-post="/invoices/{invoice.pk}/undo/"')

        self.client.post(f"/invoices/{invoice.pk}/undo/")

        self.assertEqual(on_hand(self.gloves.events.all()), before)
        self.gloves.refresh_from_db()
        self.assertEqual(str(self.gloves.price), "8.50")
        order.refresh_from_db()
        self.assertIsNone(order.received_at)
        self.assertEqual(order.qty, 10)  # still the one open order line, not split
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARSED)

    def test_undo_is_gone_after_the_window(self):
        """UX-02. An expired window is an ordinary thing to hit, so it says so
        on the invoice rather than throwing a 403 at the manager."""
        self.client.force_login(self.admin)
        self.upload()
        self.client.post("/invoices/confirm/", {})
        invoice = Invoice.objects.get()
        invoice.undo_until = timezone.now() - timedelta(seconds=1)
        invoice.save(update_fields=["undo_until"])

        response = self.client.post(f"/invoices/{invoice.pk}/undo/", follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too late to undo this import")
        self.assertNotContains(response, "Undo this import")

    def test_a_late_undo_from_the_invoice_page_goes_back_to_the_invoice(self):
        """The page's Undo posts through htmx. A plain redirect is followed out of
        sight and its page swapped into the button, taking the "too late" message
        with it, so it has to be an HX-Redirect."""
        self.client.force_login(self.admin)
        self.upload()
        self.client.post("/invoices/confirm/", {})
        invoice = Invoice.objects.get()
        invoice.undo_until = timezone.now() - timedelta(seconds=1)
        invoice.save(update_fields=["undo_until"])

        response = self.client.post(f"/invoices/{invoice.pk}/undo/", headers={"HX-Request": "true"})

        self.assertEqual((response.status_code, response["HX-Redirect"]), (200, f"/invoices/{invoice.pk}/"))
        self.assertContains(self.client.get(response["HX-Redirect"]), "Too late to undo this import")

    def test_the_invoice_keeps_an_undo_button_after_the_toast_has_gone(self):
        """UX-02. The toast carrying Undo hides itself after 6 seconds, so the
        invoice page has to carry one too for as long as the window is open."""
        self.client.force_login(self.admin)
        self.upload()
        self.client.post("/invoices/confirm/", {})
        invoice = Invoice.objects.get()

        response = self.client.get(f"/invoices/{invoice.pk}/")

        self.assertContains(response, "Undo this import")
        self.assertContains(response, f'hx-post="/invoices/{invoice.pk}/undo/"')

    def test_the_undo_window_matches_the_rest_of_the_app(self):
        """UX-02. Confirming an invoice receives stock and overwrites prices, so
        it gets at least as long to undo as marking one thing ordered does."""
        from stock import invoices as invoices_module
        from stock import views as stock_views

        self.assertGreaterEqual(invoices_module.UNDO_WINDOW, stock_views.UNDO_WINDOW)

    def test_unmatched_lines_can_be_left_or_matched_to_whats_on_order(self):
        clamps = Item.objects.create(organisation=self.org, name="Rubber dam clamps", unit="clamp", supplier=self.henry)
        bibs = Item.objects.create(organisation=self.org, name="Patient bibs", unit="pack", supplier=self.henry)
        bibs_order = OrderLine.objects.create(organisation=self.org, item=bibs, qty=5, ordered_by=self.admin)
        csv = (b"vendor,description,qty,unit\nHenry Schein,Gloves,10,8.50\n"
               b"Henry Schein,Assorted consumables,5,2.00\nHenry Schein,Rubber dam clamps,1,40.00\n")
        self.client.force_login(self.admin)
        response = self.upload(content=csv)
        self.assertContains(response, "1 matched")
        self.assertContains(response, "What did this arrive against?", count=2)

        search = self.client.get("/invoices/open-orders/", {"supplier": self.henry.pk, "q": "bib", "key": 1})
        self.assertContains(search, f'name="order_1" value="{bibs_order.pk}"')
        self.assertNotContains(search, "Gloves")
        theirs = Supplier.objects.create(organisation=Organisation.objects.create(name="Other"), name="Theirs")
        self.assertContains(self.client.get("/invoices/open-orders/", {"supplier": theirs.pk}), "Nothing on order")

        self.client.post("/invoices/confirm/", {"order_1": bibs_order.pk})  # the clamps are left unmatched
        invoice = Invoice.objects.get()
        bibs_order.refresh_from_db()
        self.assertEqual((invoice.status, bibs_order.received_qty), (Invoice.Status.PARTIAL, 5))

        clamps_order = OrderLine.objects.create(organisation=self.org, item=clamps, qty=1, ordered_by=self.admin)
        line = invoice.lines.get(description="Rubber dam clamps")
        self.assertContains(self.client.get(f"/invoices/{invoice.pk}/"), "What did this arrive against?", count=1)
        response = self.client.post(f"/invoices/line/{line.pk}/receive/", {f"order_{line.pk}": clamps_order.pk},
                                    follow=True)
        self.assertContains(response, "Received 1 clamp of Rubber dam clamps.")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.COMPLETE)

    def test_uploading_it_again_says_so_and_import_anyway_asks_why(self):
        self.client.force_login(self.admin)
        self.upload()
        self.client.post("/invoices/confirm/", {})
        response = self.upload(name="invoice (1).csv")
        repeat = Invoice.objects.get(status=Invoice.Status.IGNORED)
        self.assertRedirects(response, f"/invoices/{repeat.pk}/")
        today = timezone.localdate()
        page = self.client.get(f"/invoices/{repeat.pk}/")
        self.assertContains(page, f"Already imported on {today.day} {today:%b} - nothing changed")
        self.assertContains(page, f'href="/invoices/{repeat.pk}/check/"')

        self.assertContains(self.client.get(f"/invoices/{repeat.pk}/check/"), "Why import it again?")
        self.assertRedirects(self.client.post("/invoices/confirm/", {}), f"/invoices/{repeat.pk}/check/")
        repeat.refresh_from_db()
        self.assertEqual(repeat.status, Invoice.Status.IGNORED)

        self.client.get(f"/invoices/{repeat.pk}/check/")
        self.client.post("/invoices/confirm/", {"force_reason": "The same order arrived twice"})
        repeat.refresh_from_db()
        self.assertEqual((repeat.status, repeat.forced_by), (Invoice.Status.COMPLETE, self.admin))
        self.assertContains(self.client.get(f"/invoices/{repeat.pk}/"),
                            "Imported again by Sandy: The same order arrived twice")
        self.assertEqual(self.client.get(f"/invoices/{repeat.pk}/check/").status_code, 404)  # not twice

    def test_an_unknown_supplier_is_picked_from_the_practices_own(self):
        theirs = Supplier.objects.create(organisation=Organisation.objects.create(name="Other"), name="Theirs")
        self.client.force_login(self.admin)
        response = self.upload(content=self.CSV.replace(b"Henry Schein", b"HS Dental"))
        self.assertContains(response, 'No supplier called "HS Dental"')
        self.client.post("/invoices/confirm/", {"supplier": theirs.pk})
        invoice = Invoice.objects.get()
        self.assertEqual((invoice.supplier, invoice.status), (None, Invoice.Status.CONFLICT))
        self.assertContains(self.client.get(f"/invoices/{invoice.pk}/"), 'No supplier called "HS Dental"')

        self.client.get(f"/invoices/{invoice.pk}/check/")
        self.client.post("/invoices/confirm/", {"supplier": self.henry.pk})
        invoice.refresh_from_db()
        self.assertEqual((invoice.supplier, invoice.status, invoice.lines.count()), (self.henry, "complete", 1))

    def test_a_line_that_doesnt_add_up_is_saved_to_check_until_its_fixed(self):
        csv = b"vendor,sku,description,qty,unit,line_total\nHenry Schein,,Gloves,2,8.50,25.50\n"
        self.client.force_login(self.admin)
        self.assertContains(self.upload(content=csv), "total: doesn't add up")
        self.client.post("/invoices/confirm/", {"qty_0": "2", "price_0": "8.50", "total_0": "25.50"})
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)
        self.assertContains(self.client.get(f"/invoices/{invoice.pk}/"), "Some lines don't add up")

        self.client.get(f"/invoices/{invoice.pk}/check/")
        self.client.post("/invoices/confirm/", {"qty_0": "3", "price_0": "8.50", "total_0": "25.50"})
        invoice.refresh_from_db()
        self.assertEqual((invoice.status, invoice.lines.get().qty), (Invoice.Status.COMPLETE, 3))

    def test_a_photo_is_sent_to_gemini(self):
        self.client.force_login(self.admin)
        photo = SimpleUploadedFile("invoice.jpg", b"jpeg bytes", content_type="image/jpeg")
        reply = {"invoice_date": "2026-09-14", "vendor": "Henry Schein",
                 "lines": [{"description": "Gloves", "qty": 2, "unit": 8.5, "line_total": 17}]}
        with mock.patch("assistant.gemini.generate", return_value=reply) as generate:
            response = self.client.post("/invoices/upload/", {"invoice_file": photo})
        self.assertEqual(generate.call_args.kwargs["attachment"], ("image/jpeg", b"jpeg bytes"))
        self.assertContains(response, "Gloves")

    def test_only_pdfs_photos_and_csvs_under_5mb(self):
        self.client.force_login(self.admin)
        response = self.upload(content=b"x", content_type="text/plain", name="a.txt")
        self.assertRedirects(response, "/invoices/")

    def test_a_failed_read_goes_back_with_a_message(self):
        self.client.force_login(self.admin)
        photo = SimpleUploadedFile("invoice.jpg", b"x", content_type="image/jpeg")
        with mock.patch("assistant.gemini.generate", side_effect=gemini.GeminiError):
            response = self.client.post("/invoices/upload/", {"invoice_file": photo}, follow=True)
        # On the page, not only in a toast, and it names the file (UX-10).
        self.assertContains(response, "Couldn't read invoice.jpg")
        self.assertContains(response, "A clearer photo usually does it")

    def test_cancelling_leaves_no_rows_behind(self):
        self.client.force_login(self.admin)
        self.upload()
        self.client.get("/invoices/")  # cancel: just navigate away, nothing to clean up
        self.assertFalse(Invoice.objects.exists())

    def test_managers_only(self):
        self.client.force_login(self.assistant)
        self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.client.get("/invoices/").status_code, 403)


@override_settings(AI_API_KEY="test-key")
class InvoiceBatchTests(Practice):
    def csv(self, number):
        body = f"vendor,invoice_number,description,qty,unit\nHenry Schein,INV-{number},Gloves,1,8.50\n"
        return SimpleUploadedFile(f"INV-{number}.csv", body.encode(), "text/csv")

    def upload(self, files):
        with mock.patch("stock.invoices.subprocess.Popen") as popen, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/invoices/batch/", {"invoice_files": files})
        return response, popen

    def test_several_invoices_start_a_batch_listed_file_by_file(self):
        self.client.force_login(self.admin)
        response, popen = self.upload([self.csv(1), self.csv(2)])
        batch = InvoiceBatch.objects.get()
        self.assertRedirects(response, f"/invoices/batch/{batch.pk}/")
        self.assertEqual(popen.call_args.args[0][-2:], ["import_invoices", str(batch.pk)])
        page = self.client.get(f"/invoices/batch/{batch.pk}/")
        self.assertContains(page, "INV-1.csv")
        self.assertContains(page, "Waiting", count=2)
        self.assertContains(page, 'hx-trigger="every 2s"')
        self.assertContains(page, "Resume")
        self.assertContains(self.client.get("/invoices/"), "2 of 2 left")

        run_batch(batch)
        fragment = self.client.get(f"/invoices/batch/{batch.pk}/", headers={"HX-Request": "true"})
        self.assertNotContains(fragment, "<html")
        self.assertContains(fragment, "2 of 2 done")
        self.assertContains(fragment, "Done", count=2)
        self.assertNotContains(fragment, "every 2s")
        self.assertNotContains(fragment, "Resume")
        self.assertNotContains(self.client.get("/invoices/"), "Still importing")

    def test_resume_starts_another_run_only_while_files_are_left(self):
        self.client.force_login(self.admin)
        self.upload([self.csv(1)])
        batch = InvoiceBatch.objects.get()
        with mock.patch("stock.invoices.subprocess.Popen") as popen:
            self.assertRedirects(self.client.post(f"/invoices/batch/{batch.pk}/resume/"), f"/invoices/batch/{batch.pk}/")
            popen.assert_called_once()
            run_batch(batch)
            self.client.post(f"/invoices/batch/{batch.pk}/resume/")
            popen.assert_called_once()

    def test_only_invoice_files_under_5mb(self):
        self.client.force_login(self.admin)
        response, popen = self.upload([self.csv(1), SimpleUploadedFile("notes.txt", b"x", "text/plain")])
        self.assertRedirects(response, "/invoices/")
        self.assertFalse(InvoiceBatch.objects.exists())
        popen.assert_not_called()

    @override_settings(CACHES=LOCMEM, AI_DAILY_LIMIT=3)
    def test_every_photo_or_pdf_counts_towards_the_daily_ai_limit(self):
        cache.clear()
        self.client.force_login(self.admin)
        photos = [SimpleUploadedFile(f"{n}.jpg", b"jpeg bytes", "image/jpeg") for n in range(4)]
        response, _ = self.upload(photos)
        self.assertRedirects(response, "/invoices/", fetch_redirect_response=False)
        # Says when it will work again, and points at the path that still does (UX-10).
        page = self.client.get("/invoices/")
        self.assertContains(page, "as many files as it can today")
        self.assertContains(page, "upload a CSV instead")
        self.assertFalse(InvoiceBatch.objects.exists())

    def test_managers_only(self):
        self.client.force_login(self.assistant)
        response, _ = self.upload([self.csv(1)])
        self.assertEqual(response.status_code, 403)


@override_settings(AI_API_KEY="test-key", AI_MODEL="gemini-test")
class GeminiClientTests(SimpleTestCase):
    def call(self, body, **kwargs):
        with mock.patch("assistant.gemini.urllib.request.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(body).encode())
            result = gemini.generate("Be brief.", [("user", "Hi")], **kwargs)
        return result, urlopen.call_args.args[0]

    def test_sends_the_prompt_and_key_and_returns_the_text(self):
        text, request = self.call({"candidates": [{"content": {"parts": [{"text": "Kia ora"}, {"text": "!"}]}}]})
        self.assertEqual(text, "Kia ora!")
        self.assertIn("/models/gemini-test:generateContent", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        body = json.loads(request.data)
        self.assertEqual(body["system_instruction"], {"parts": [{"text": "Be brief."}]})
        self.assertEqual(body["contents"], [{"role": "user", "parts": [{"text": "Hi"}]}])
        self.assertEqual(body["generationConfig"], {"thinkingConfig": {"thinkingLevel": "low"}})

    def test_structured_answers_and_attachments(self):
        rows, request = self.call({"candidates": [{"content": {"parts": [{"text": '[{"name": "Gloves"}]'}]}}]},
                                  schema={"type": "ARRAY"}, attachment=("image/png", b"png"))
        self.assertEqual(rows, [{"name": "Gloves"}])
        body = json.loads(request.data)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["contents"][0]["parts"][1], {"inline_data": {"mime_type": "image/png", "data": "cG5n"}})

    @override_settings(AI_EMBEDDING_MODEL="embed-test")
    def test_embeddings_come_back_in_order(self):
        with mock.patch("assistant.gemini.urllib.request.urlopen") as urlopen:
            reply = {"embeddings": [{"values": [0.1, 0.2]}, {"values": [0.3, 0.4]}]}
            urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(reply).encode())
            self.assertEqual(gemini.embed(["gloves", "bibs"]), [[0.1, 0.2], [0.3, 0.4]])
        request = urlopen.call_args.args[0]
        self.assertIn("/models/embed-test:batchEmbedContents", request.full_url)
        body = json.loads(request.data)
        self.assertEqual([r["content"]["parts"][0]["text"] for r in body["requests"]], ["gloves", "bibs"])
        self.assertEqual(body["requests"][0]["model"], "models/embed-test")
        with (
            mock.patch("assistant.gemini.urllib.request.urlopen") as urlopen,
            self.assertLogs("assistant.gemini"),
            self.assertRaises(gemini.GeminiError),
        ):
            urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(reply).encode())
            gemini.embed(["just one"])

    def test_blocked_empty_or_failed_answers_raise(self):
        for body in ({"promptFeedback": {"blockReason": "SAFETY"}}, {"candidates": [{"content": {"parts": [{"text": " "}]}}]}):
            with self.subTest(body=body), self.assertLogs("assistant.gemini"), self.assertRaises(gemini.GeminiError):
                self.call(body)
        error = urllib.error.HTTPError("url", 429, "Too many", {}, io.BytesIO(b"quota"))
        with (
            mock.patch("assistant.gemini.urllib.request.urlopen", side_effect=error),
            self.assertLogs("assistant.gemini") as logs,
            self.assertRaises(gemini.GeminiError),
        ):
            gemini.generate("x", [("user", "Hi")])
        self.assertIn("429", logs.output[0])


@override_settings(AI_API_KEY="test-key")
class UploadFailureTests(Practice):
    """UX-10. Every failed read redirected to the upload page with a toast. The
    toast hides itself after six seconds and the page looks exactly as it did
    before, so a manager who glanced away couldn't tell whether anything had
    happened - and uploaded the same failing file again, spending another AI
    call on it."""

    CSV = b"vendor,sku,description,qty,unit\nHenry Schein,,Gloves,2,8.50\n"

    def post(self, upload):
        return self.client.post("/invoices/upload/", {"invoice_file": upload}, follow=True)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def test_a_file_that_is_too_big_says_so_and_says_how_big(self):
        big = SimpleUploadedFile("scan.jpg", b"x" * (6 * 1024 * 1024), content_type="image/jpeg")

        response = self.post(big)

        self.assertContains(response, "Couldn't read scan.jpg")
        self.assertContains(response, "6.0 MB")
        self.assertContains(response, "takes up to 5 MB")

    def test_the_wrong_sort_of_file_says_what_it_does_take(self):
        response = self.post(SimpleUploadedFile("notes.docx", b"x", content_type="application/msword"))

        self.assertContains(response, "Couldn't read notes.docx")
        self.assertContains(response, "reads photos, PDFs and CSVs")

    def test_choosing_nothing_says_so_rather_than_naming_no_file(self):
        response = self.client.post("/invoices/upload/", {}, follow=True)

        self.assertContains(response, "Nothing was read")
        self.assertContains(response, "No file was chosen")

    def test_the_message_is_in_the_page_not_only_in_a_toast(self):
        self.client.post("/invoices/upload/",
                         {"invoice_file": SimpleUploadedFile("notes.docx", b"x", content_type="application/msword")})

        # In the page's own HTML, so it is still there after the toast has gone.
        page = self.client.get("/invoices/")
        self.assertContains(page, "Couldn't read notes.docx")
        self.assertContains(page, 'role="alert"')
        # But not on the next load: a message, not a nag.
        self.assertNotContains(self.client.get("/invoices/"), "Couldn't read notes.docx")

    def test_a_personal_limit_says_an_hour_and_offers_the_csv_path(self):
        cache.clear()
        with mock.patch("assistant.views.ai_limited", return_value=True), \
             mock.patch("assistant.views.ai_limit_is_personal", return_value=True):
            response = self.post(SimpleUploadedFile("inv.jpg", b"x", content_type="image/jpeg"))

        self.assertContains(response, "Try again in an hour")
        self.assertContains(response, "upload a CSV instead")

    def test_the_shared_daily_limit_says_tomorrow_instead(self):
        cache.clear()
        with mock.patch("assistant.views.ai_limited", return_value=True), \
             mock.patch("assistant.views.ai_limit_is_personal", return_value=False):
            response = self.post(SimpleUploadedFile("inv.jpg", b"x", content_type="image/jpeg"))

        self.assertContains(response, "as many files as it can today")
        self.assertContains(response, "Try again tomorrow")

    def test_a_csv_still_works_when_the_ai_limit_is_hit(self):
        """The advice has to be true: a CSV is read without Gemini."""
        cache.clear()
        with mock.patch("assistant.views.ai_limited", return_value=True):
            response = self.post(SimpleUploadedFile("inv.csv", self.CSV, content_type="text/csv"))

        self.assertNotContains(response, "upload a CSV instead")
        self.assertContains(response, "Gloves")

    def test_too_many_files_at_once_says_how_many_is_too_many(self):
        files = [SimpleUploadedFile(f"{n}.csv", self.CSV, "text/csv") for n in range(MAX_BATCH + 1)]

        response = self.client.post("/invoices/batch/", {"invoice_files": files}, follow=True)

        self.assertContains(response, f"{MAX_BATCH + 1} files")
        self.assertContains(response, "smaller lots")
