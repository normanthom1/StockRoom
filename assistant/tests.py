import io
import json
import urllib.error
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organisation, User
from accounts.ratelimit import AI_PER_HOUR
from stock.models import DemoResetState, Item, OrderLine, StockEvent, Supplier

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

    def test_structured_answers_and_attachments(self):
        rows, request = self.call({"candidates": [{"content": {"parts": [{"text": '[{"name": "Gloves"}]'}]}}]},
                                  schema={"type": "ARRAY"}, attachment=("image/png", b"png"))
        self.assertEqual(rows, [{"name": "Gloves"}])
        body = json.loads(request.data)
        self.assertEqual(body["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(body["contents"][0]["parts"][1], {"inline_data": {"mime_type": "image/png", "data": "cG5n"}})

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
