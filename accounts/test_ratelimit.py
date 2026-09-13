from django.contrib.auth.password_validation import get_default_password_validators
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Organisation, User

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()

    def login(self, email="sandy@example.com", ip="203.0.113.1", **extra):
        return self.client.post(reverse("login"), {"username": email, "password": "wrong"}, REMOTE_ADDR=ip, **extra)

    def test_five_tries_on_one_account_then_429(self):
        for _ in range(5):
            self.assertEqual(self.login().status_code, 200)  # the form, with "wrong password"
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many tries", status_code=429)

    def test_account_limit_holds_across_addresses(self):
        for n in range(5):
            self.login(ip=f"203.0.113.{n}")
        self.assertEqual(self.login(ip="198.51.100.9").status_code, 429)

    def test_account_limit_ignores_case_and_spaces(self):
        for _ in range(5):
            self.login(email="Sandy@Example.com ")
        self.assertEqual(self.login(email="sandy@example.com").status_code, 429)

    def test_one_office_connection_can_log_in_many_staff(self):
        # Staff share the practice's IP; the per-IP limit is looser than per-account.
        for n in range(30):
            self.assertEqual(self.login(email=f"staff{n}@example.com").status_code, 200)
        self.assertEqual(self.login(email="one-too-many@example.com").status_code, 429)
        self.assertEqual(self.login(email="elsewhere@example.com", ip="198.51.100.9").status_code, 200)

    def test_viewing_the_login_page_is_never_limited(self):
        for _ in range(6):
            self.login()
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)

    def test_real_login_still_works_under_the_limit(self):
        org = Organisation.objects.create(name="Test Dental")
        User.objects.create_user("liz@example.com", "correct-horse-battery", organisation=org)
        self.login(email="liz@example.com")
        response = self.client.post(reverse("login"), {"username": "liz@example.com", "password": "correct-horse-battery"},
                                    REMOTE_ADDR="203.0.113.1")
        self.assertRedirects(response, reverse("stock:home"), fetch_redirect_response=False)

    @override_settings(CLIENT_IP_HEADER="HTTP_X_REAL_IP")
    def test_behind_railway_the_visitor_address_comes_from_x_real_ip(self):
        # Every request arrives from the proxy's REMOTE_ADDR; X-Real-IP tells visitors apart.
        for n in range(30):
            self.login(email=f"staff{n}@example.com", HTTP_X_REAL_IP="203.0.113.1")
        self.assertEqual(self.login(email="x@example.com", HTTP_X_REAL_IP="203.0.113.1").status_code, 429)
        self.assertEqual(self.login(email="y@example.com", HTTP_X_REAL_IP="198.51.100.9").status_code, 200)

    def test_signup_is_limited_to_five_an_hour_per_address(self):
        for n in range(5):
            self.client.post(reverse("signup"), {"email": f"x{n}@example.com"}, REMOTE_ADDR="203.0.113.1")
        response = self.client.post(reverse("signup"), {"email": "x@example.com"}, REMOTE_ADDR="203.0.113.1")
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Wait 60 minutes", status_code=429)

    def test_ten_wrong_codes_lock_the_code_pad_for_that_practice(self):
        org = Organisation.objects.create(name="Test Dental")
        User.objects.create_user("front@example.com", "pw", organisation=org, role=User.Role.ADMIN, is_practice_login=True)
        User.objects.create_staff(org, "Liz", "22")
        self.client.post(reverse("login"), {"username": "front@example.com", "password": "pw"})
        for _ in range(10):
            self.assertEqual(self.client.post(reverse("enter_code"), {"pin": "99"}).status_code, 200)
        # Locked even for the right code, so guessing can't carry on underneath.
        self.assertEqual(self.client.post(reverse("enter_code"), {"pin": "22"}).status_code, 429)

    def test_right_codes_never_count_towards_the_lock(self):
        org = Organisation.objects.create(name="Test Dental")
        User.objects.create_user("front@example.com", "pw", organisation=org, role=User.Role.ADMIN, is_practice_login=True)
        User.objects.create_staff(org, "Liz", "22")
        self.client.post(reverse("login"), {"username": "front@example.com", "password": "pw"})
        for _ in range(12):  # a busy morning of handing the device around
            self.assertRedirects(self.client.post(reverse("enter_code"), {"pin": "22"}), reverse("stock:home"),
                                 fetch_redirect_response=False)

    def test_django_admin_login_is_limited_too(self):
        url = reverse("admin:login")
        for _ in range(5):
            self.client.post(url, {"username": "root@example.com", "password": "guess"}, REMOTE_ADDR="203.0.113.1")
        response = self.client.post(url, {"username": "root@example.com", "password": "guess"}, REMOTE_ADDR="203.0.113.1")
        self.assertEqual(response.status_code, 429)


class PasswordRulesTests(TestCase):
    def test_validators_are_on(self):
        names = {type(v).__name__ for v in get_default_password_validators()}
        self.assertEqual(names, {"UserAttributeSimilarityValidator", "MinimumLengthValidator",
                                 "CommonPasswordValidator", "NumericPasswordValidator"})

    def test_signup_rejects_a_weak_password(self):
        response = self.client.post(reverse("signup"), {
            "practice_name": "Smile Dental", "name": "Sandy", "email": "sandy@smile.test",
            "password": "password",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="sandy@smile.test").exists())
