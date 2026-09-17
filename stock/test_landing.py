from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Organisation, User

from .models import DemoResetState


class LandingPageTests(TestCase):
    def test_someone_logged_out_sees_what_stockroom_is(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Know what to order before the shelf is empty.")
        self.assertContains(response, "What it doesn't do")
        self.assertContains(response, 'href="/accounts/login/"')

    def test_no_em_or_en_dashes(self):
        content = self.client.get("/").content.decode()
        self.assertNotIn("—", content)
        self.assertNotIn("–", content)

    @override_settings(DEMO_MODE=True, SIGNUP_ENABLED=False)
    def test_the_demo_is_the_first_thing_to_try_on_the_demo_site(self):
        DemoResetState.objects.create(pk=1, date=timezone.localdate())  # no reseed mid-test
        response = self.client.get("/")
        self.assertContains(response, "Try the demo practice")
        self.assertContains(response, 'action="/accounts/demo-login/"')
        self.assertNotContains(response, "Set up your practice")

    @override_settings(DEMO_MODE=False, SIGNUP_ENABLED=True)
    def test_elsewhere_it_offers_setting_up_a_practice(self):
        response = self.client.get("/")
        self.assertContains(response, "Set up your practice")
        self.assertNotContains(response, "Try the demo practice")

    @override_settings(AI_API_KEY="test-key")
    def test_the_invoice_features_are_described_when_ai_is_on(self):
        content = self.client.get("/").content.decode()
        self.assertIn("Start with the paperwork you already have", content)
        self.assertIn("The invoice closes the loop", content)
        self.assertIn("GST and tax records", content)

    @override_settings(AI_API_KEY="")
    def test_nothing_ai_is_promised_when_there_is_no_key(self):
        """Invoice upload and Ask StockRoom 404 without a key (ai_required), so
        the public page mustn't advertise them."""
        content = self.client.get("/").content.decode()
        for claim in ["Start with the paperwork you already have", "The invoice closes the loop",
                      "Ask StockRoom", "GST and tax records", "Google Gemini"]:
            with self.subTest(claim):
                self.assertNotIn(claim, content)

    @override_settings(AI_API_KEY="test-key")
    def test_it_says_invoice_files_are_kept_not_just_stock_and_staff(self):
        """The page makes a promise about what StockRoom holds. Invoices are
        now kept for 7 years, so leaving them out would make it untrue."""
        content = self.client.get("/").content.decode()
        self.assertIn("the invoice files you upload", content)
        self.assertIn("Nothing about patients.", content)

    def test_logged_in_staff_still_get_their_home_screen(self):
        org = Organisation.objects.create(name="Test Dental")
        self.client.force_login(User.objects.create_user("sandy@example.com", "pw", organisation=org))
        response = self.client.get("/")
        self.assertContains(response, "What to order today")
        self.assertNotContains(response, "Know what to order before the shelf is empty.")
