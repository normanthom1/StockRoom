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

    def test_logged_in_staff_still_get_their_home_screen(self):
        org = Organisation.objects.create(name="Test Dental")
        self.client.force_login(User.objects.create_user("sandy@example.com", "pw", organisation=org))
        response = self.client.get("/")
        self.assertContains(response, "What to order today")
        self.assertNotContains(response, "Know what to order before the shelf is empty.")
