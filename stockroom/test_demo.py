"""Demo mode (#35): one-click sign-in, the nightly reset with no separate
Railway service, and sign-up hidden by default in demo mode.
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Organisation, User
from stock.models import DemoResetState
from stockroom.demo import DEMO_EMAIL, DEMO_ORG, DEMO_PASSWORD, DemoResetMiddleware

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class DemoLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        org = Organisation.objects.create(name=DEMO_ORG)
        User.objects.create_user(DEMO_EMAIL, DEMO_PASSWORD, organisation=org, role=User.Role.ADMIN, is_practice_login=True)
        User.objects.create_staff(org, "Sofia", "0000", User.Role.ADMIN)
        User.objects.create_staff(org, "Johanna", "11")
        # Already reset today, so DemoResetMiddleware leaves these fixtures alone.
        DemoResetState.objects.create(pk=1, date=timezone.localdate())

    @override_settings(DEMO_MODE=True)
    def test_one_click_opens_the_demo_practice_at_its_code_pad(self):
        response = self.client.post(reverse("demo_login"))
        self.assertRedirects(response, reverse("enter_code"))
        content = self.client.get(reverse("enter_code")).content.decode()
        self.assertIn("Sofia 0000", content)
        self.assertIn("Johanna 11", content)

    @override_settings(DEMO_MODE=True)
    def test_the_practice_login_works_by_hand_too(self):
        self.client.post(reverse("login"), {"username": DEMO_EMAIL, "password": DEMO_PASSWORD})
        self.client.post(reverse("enter_code"), {"pin": "11"})
        self.assertContains(self.client.get(reverse("stock:home")), "Johanna")

    def test_disabled_when_demo_mode_is_off(self):
        with override_settings(DEMO_MODE=False):
            response = self.client.post(reverse("demo_login"))
        self.assertEqual(response.status_code, 404)

    @override_settings(DEMO_MODE=True)
    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("demo_login")).status_code, 405)

    @override_settings(DEMO_MODE=True)
    def test_only_the_demo_practice_is_reachable(self):
        Organisation.objects.filter(name=DEMO_ORG).update(name="Renamed Dental")
        self.assertEqual(self.client.post(reverse("demo_login")).status_code, 404)

    @override_settings(DEMO_MODE=True)
    def test_a_real_practices_codes_are_never_shown(self):
        org = Organisation.objects.create(name="Real Practice")
        User.objects.create_user("front@real.test", "pw", organisation=org, role=User.Role.ADMIN, is_practice_login=True)
        User.objects.create_staff(org, "Private Person", "42")
        self.client.post(reverse("login"), {"username": "front@real.test", "password": "pw"})
        self.assertNotContains(self.client.get(reverse("enter_code")), "Private Person")

    @override_settings(DEMO_MODE=True)
    def test_login_page_shows_the_one_click_button_login_and_reset_notice(self):
        content = self.client.get(reverse("login")).content.decode()
        self.assertIn("Sign in to the demo practice", content)
        self.assertIn(DEMO_EMAIL, content)
        self.assertIn("Demo data resets every night.", content)

    def test_login_page_hides_them_outside_demo_mode(self):
        content = self.client.get(reverse("login")).content.decode()
        self.assertNotIn("demo practice", content)
        self.assertNotIn(DEMO_EMAIL, content)
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
        self.assertFalse(Organisation.objects.filter(name=DEMO_ORG).exists())

    @override_settings(DEMO_MODE=True)
    def test_first_request_ever_seeds_the_demo_and_records_todays_date(self):
        self.client.get(reverse("login"))
        self.assertTrue(Organisation.objects.filter(name=DEMO_ORG).exists())
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
    def test_static_paths_are_skipped_explicitly(self):
        # In production WhiteNoise answers /static/ before this middleware
        # ever runs - but only once `collectstatic` has populated
        # STATIC_ROOT, which hasn't happened in this test environment. Drive
        # the middleware directly so the check doesn't depend on that.
        middleware = DemoResetMiddleware(get_response=lambda request: None)
        middleware(RequestFactory().get("/static/vendor/htmx.min.js"))
        self.assertFalse(DemoResetState.objects.exists())
