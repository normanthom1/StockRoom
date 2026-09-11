import re

from django.contrib.auth import authenticate
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings

from .models import Organisation, User


class OrganisationTests(TestCase):
    def test_slug_is_generated_and_kept_unique(self):
        first = Organisation.objects.create(name="Ōtāhuhu Dental")
        second = Organisation.objects.create(name="Ōtāhuhu Dental")
        self.assertEqual(first.slug, "otahuhu-dental")
        self.assertEqual(second.slug, "otahuhu-dental-2")

    def test_timezone_defaults_to_auckland_and_rejects_nonsense(self):
        org = Organisation(name="Test Dental")
        self.assertEqual(org.timezone, "Pacific/Auckland")
        org.timezone = "Mars/Olympus"
        with self.assertRaises(ValidationError):
            org.full_clean()


class UserTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")

    def test_new_users_are_assistants_by_default(self):
        user = User.objects.create_user("liz@example.com", "pw", organisation=self.org)
        self.assertEqual(user.role, User.Role.ASSISTANT)

    def test_login_by_email_ignores_case(self):
        User.objects.create_user("Sandy@Example.com", "s3cret-pass", organisation=self.org)
        self.assertIsNotNone(authenticate(email="sandy@example.COM", password="s3cret-pass"))

    def test_email_is_unique_regardless_of_case(self):
        User.objects.create_user("sandy@example.com", "pw", organisation=self.org)
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("SANDY@example.com", "pw", organisation=self.org)

    def test_staff_must_belong_to_a_practice(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user("nobody@example.com", "pw")

    def test_superusers_need_no_practice(self):
        admin = User.objects.create_superuser("tom@example.com", "pw")
        self.assertIsNone(admin.organisation)

    def test_is_org_admin(self):
        manager = User.objects.create_user("sandy@example.com", "pw", organisation=self.org, role=User.Role.ADMIN)
        assistant = User.objects.create_user("liz@example.com", "pw", organisation=self.org)
        platform = User.objects.create_superuser("tom@example.com", "pw", role=User.Role.ADMIN)
        self.assertTrue(manager.is_org_admin)
        self.assertFalse(assistant.is_org_admin)
        # A superuser has no practice to administer, whatever their role says.
        self.assertFalse(platform.is_org_admin)


class AuthFlowTests(TestCase):
    signup_data = {
        "practice_name": "Kowhai Dental",
        "name": "Sandy Ngata",
        "email": "sandy@kowhai.test",
        "password": "gloves-and-gauze-42",
    }

    def test_pages_render(self):
        for url in ["/accounts/login/", "/accounts/signup/", "/accounts/password_reset/",
                    "/accounts/password_reset/done/", "/accounts/reset/done/"]:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_signup_creates_the_practice_and_its_manager(self):
        response = self.client.post("/accounts/signup/", self.signup_data)
        self.assertRedirects(response, "/")
        user = User.objects.get(email="sandy@kowhai.test")
        self.assertEqual(user.name, "Sandy Ngata")
        self.assertEqual(user.organisation.name, "Kowhai Dental")
        self.assertTrue(user.is_org_admin)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_signup_rejects_an_email_in_use_in_any_case(self):
        User.objects.create_user("sandy@kowhai.test", "pw", organisation=Organisation.objects.create(name="Other"))
        response = self.client.post("/accounts/signup/", {**self.signup_data, "email": "SANDY@kowhai.test"})
        self.assertContains(response, "Someone already uses that email address")
        self.assertFalse(Organisation.objects.filter(name="Kowhai Dental").exists())

    def test_signup_rejects_a_weak_password(self):
        response = self.client.post("/accounts/signup/", {**self.signup_data, "password": "password"})
        self.assertContains(response, "This password is too common")
        self.assertFalse(User.objects.filter(email="sandy@kowhai.test").exists())

    @override_settings(SIGNUP_ENABLED=False)
    def test_signup_can_be_switched_off(self):
        self.assertEqual(self.client.get("/accounts/signup/").status_code, 404)
        self.assertNotContains(self.client.get("/accounts/login/"), "/accounts/signup/")

    def test_log_in_with_email_in_any_case_then_log_out(self):
        self.client.post("/accounts/signup/", self.signup_data)
        self.assertEqual(self.client.get("/accounts/logout/").status_code, 405)  # POST only
        self.assertRedirects(self.client.post("/accounts/logout/"), "/accounts/login/")
        self.assertEqual(self.client.get("/").status_code, 302)

        response = self.client.post(
            "/accounts/login/", {"username": "Sandy@Kowhai.test", "password": self.signup_data["password"]}
        )
        self.assertRedirects(response, "/")

    def test_password_reset_link_sets_a_new_password(self):
        self.client.post("/accounts/signup/", self.signup_data)
        self.client.post("/accounts/logout/")

        self.client.post("/accounts/password_reset/", {"email": "sandy@kowhai.test"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset your StockRoom password")
        link = re.search(r"http://testserver(/accounts/reset/\S+)", mail.outbox[0].body).group(1)

        form_page = self.client.get(link, follow=True)  # swaps the token into the session
        set_password_url = form_page.redirect_chain[-1][0]
        response = self.client.post(
            set_password_url, {"new_password1": "fresh-bibs-and-tips-7", "new_password2": "fresh-bibs-and-tips-7"}
        )
        self.assertRedirects(response, "/accounts/reset/done/")
        self.assertTrue(self.client.login(email="sandy@kowhai.test", password="fresh-bibs-and-tips-7"))

    def test_password_reset_for_an_unknown_email_sends_nothing_and_says_the_same(self):
        response = self.client.post("/accounts/password_reset/", {"email": "nobody@nowhere.test"})
        self.assertRedirects(response, "/accounts/password_reset/done/")
        self.assertEqual(len(mail.outbox), 0)

    def test_change_password(self):
        self.client.post("/accounts/signup/", self.signup_data)
        response = self.client.post(
            "/accounts/password_change/",
            {
                "old_password": self.signup_data["password"],
                "new_password1": "fresh-bibs-and-tips-7",
                "new_password2": "fresh-bibs-and-tips-7",
            },
        )
        self.assertRedirects(response, "/accounts/password_change/done/")
        self.assertTrue(self.client.login(email="sandy@kowhai.test", password="fresh-bibs-and-tips-7"))
