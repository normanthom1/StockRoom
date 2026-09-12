"""Demo mode (#35): one-click sign-in, the nightly reset with no separate
Railway service, and sign-up hidden by default in demo mode.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from accounts.views import DEMO_LOGINS
from stock.models import DemoResetState

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class DemoLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organisation.objects.create(name="Demo Dental")
        User.objects.create_user("sandy@demodental.test", "DemoPass123", organisation=org, role=User.Role.ADMIN)

    @override_settings(DEMO_MODE=True)
    def test_one_click_login_works_for_a_mapped_demo_user(self):
        response = self.client.post(reverse("demo_login", args=["sandy"]))
        self.assertRedirects(response, reverse("stock:home"))
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_disabled_when_demo_mode_is_off(self):
        with override_settings(DEMO_MODE=False):
            response = self.client.post(reverse("demo_login", args=["sandy"]))
        self.assertEqual(response.status_code, 404)

    @override_settings(DEMO_MODE=True)
    def test_unknown_who_is_404(self):
        self.assertEqual(self.client.post(reverse("demo_login", args=["someone-else"])).status_code, 404)

    @override_settings(DEMO_MODE=True)
    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("demo_login", args=["sandy"])).status_code, 405)

    @override_settings(DEMO_MODE=True)
    def test_a_mapped_email_belonging_to_a_different_org_is_not_found(self):
        # Emails are globally unique, so this can't collide with "sandy" above - it
        # documents that the lookup is scoped to "Demo Dental", not just any user.
        User.objects.create_user("someone@realpractice.test", "pw", organisation=Organisation.objects.create(name="Real Practice"))
        with patch.dict(DEMO_LOGINS, {"sandy": ("someone@realpractice.test", "Sandy (manager)")}):
            self.assertEqual(self.client.post(reverse("demo_login", args=["sandy"])).status_code, 404)

    @override_settings(DEMO_MODE=True)
    def test_login_page_shows_the_one_click_buttons_and_reset_notice(self):
        content = self.client.get(reverse("login")).content.decode()
        self.assertIn("Sign in as Sandy (manager)", content)
        self.assertIn("Sign in as Johanna (assistant)", content)
        self.assertIn("Sign in as Practice Owner", content)
        self.assertIn("Demo data resets every night.", content)

    def test_login_page_hides_them_outside_demo_mode(self):
        content = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("Sign in as Sandy", content)
        self.assertNotIn("resets every night", content)


class SignupVisibilityTests(TestCase):
    @override_settings(DEMO_MODE=True, SIGNUP_ENABLED=False)
    def test_signup_is_hidden_by_default_in_demo_mode(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 404)
        self.assertNotIn("Set up your practice", self.client.get(reverse("login")).content.decode())

    @override_settings(DEMO_MODE=True, SIGNUP_ENABLED=True)
    def test_signup_can_be_turned_back_on_in_demo_mode(self):
        self.assertEqual(self.client.get(reverse("signup")).status_code, 200)


@override_settings(CACHES=LOCMEM)
class DemoResetMiddlewareTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(DEMO_MODE=False)
    def test_does_nothing_outside_demo_mode(self):
        self.client.get(reverse("login"))
        self.assertFalse(DemoResetState.objects.exists())
        self.assertFalse(Organisation.objects.filter(name="Demo Dental").exists())

    @override_settings(DEMO_MODE=True)
    def test_first_request_ever_seeds_the_demo_and_records_todays_date(self):
        self.client.get(reverse("login"))
        self.assertTrue(Organisation.objects.filter(name="Demo Dental").exists())
        self.assertEqual(DemoResetState.objects.get(pk=1).date, timezone.localdate())

    @override_settings(DEMO_MODE=True)
    def test_a_second_request_the_same_day_does_not_reset_again(self):
        self.client.get(reverse("login"))
        with patch("stockroom.demo.call_command") as call_command:
            self.client.get(reverse("login"))
        call_command.assert_not_called()

    @override_settings(DEMO_MODE=True)
    def test_a_request_the_next_day_resets_again(self):
        self.client.get(reverse("login"))
        DemoResetState.objects.update(date=timezone.localdate() - timedelta(days=1))
        cache.clear()  # yesterday's lock (for a real "next day" this simply wouldn't exist yet)
        with patch("stockroom.demo.call_command") as call_command:
            self.client.get(reverse("login"))
        call_command.assert_called_once_with("seed_demo", reset=True)
        self.assertEqual(DemoResetState.objects.get(pk=1).date, timezone.localdate())

    @override_settings(DEMO_MODE=True)
    def test_a_request_that_loses_the_lock_race_skips_the_reset(self):
        today = timezone.localdate()
        cache.set(f"demo-reset-lock:{today}", True, 120)  # simulate another request winning the race
        with patch("stockroom.demo.call_command") as call_command:
            self.client.get(reverse("login"))
        call_command.assert_not_called()
        self.assertFalse(DemoResetState.objects.exists())

    @override_settings(DEMO_MODE=True)
    def test_static_files_do_not_trigger_a_check(self):
        # WhiteNoise serves /static/ before this middleware runs at all.
        self.client.get("/static/vendor/htmx.min.js")
        self.assertFalse(DemoResetState.objects.exists())
