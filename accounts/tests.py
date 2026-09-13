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
        "email": "reception@kowhai.test",
        "password": "gloves-and-gauze-42",
        "name": "Sandy Ngata",
        "pin": "4821",
    }

    def test_pages_render(self):
        for url in ["/accounts/login/", "/accounts/signup/", "/accounts/password_reset/",
                    "/accounts/password_reset/done/", "/accounts/reset/done/"]:
            with self.subTest(url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_signup_creates_the_practice_login_and_its_first_manager(self):
        response = self.client.post("/accounts/signup/", self.signup_data)
        self.assertRedirects(response, "/")
        practice = User.objects.get(email="reception@kowhai.test")
        self.assertTrue(practice.is_practice_login)
        self.assertEqual(practice.organisation.name, "Kowhai Dental")
        manager = User.objects.get(organisation=practice.organisation, pin="4821")
        self.assertEqual(manager.name, "Sandy Ngata")
        self.assertTrue(manager.is_org_admin)
        self.assertFalse(manager.has_usable_password())
        # Straight in as the manager, on a session the practice login opened.
        self.assertEqual(int(self.client.session["_auth_user_id"]), manager.pk)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_signup_rejects_an_email_in_use_in_any_case(self):
        User.objects.create_user("reception@kowhai.test", "pw", organisation=Organisation.objects.create(name="Other"))
        response = self.client.post("/accounts/signup/", {**self.signup_data, "email": "RECEPTION@kowhai.test"})
        self.assertContains(response, "Someone already uses that email address")
        self.assertFalse(Organisation.objects.filter(name="Kowhai Dental").exists())

    def test_signup_rejects_a_weak_password(self):
        response = self.client.post("/accounts/signup/", {**self.signup_data, "password": "password"})
        self.assertContains(response, "This password is too common")
        self.assertFalse(User.objects.filter(email="reception@kowhai.test").exists())

    def test_the_first_manager_needs_a_four_digit_code(self):
        for pin in ["07", "48a1"]:
            with self.subTest(pin):
                response = self.client.post("/accounts/signup/", {**self.signup_data, "pin": pin})
                self.assertEqual(response.status_code, 200)
        self.assertFalse(Organisation.objects.filter(name="Kowhai Dental").exists())

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
            "/accounts/login/", {"username": "Reception@Kowhai.test", "password": self.signup_data["password"]}
        )
        # The practice login alone only reaches the code pad.
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/"), "/accounts/code/")

    def test_password_reset_link_sets_a_new_password(self):
        self.client.post("/accounts/signup/", self.signup_data)
        self.client.post("/accounts/logout/")

        self.client.post("/accounts/password_reset/", {"email": "reception@kowhai.test"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, "Reset your StockRoom password")
        link = re.search(r"http://testserver(/accounts/reset/\S+)", mail.outbox[0].body).group(1)

        form_page = self.client.get(link, follow=True)  # swaps the token into the session
        set_password_url = form_page.redirect_chain[-1][0]
        response = self.client.post(
            set_password_url, {"new_password1": "fresh-bibs-and-tips-7", "new_password2": "fresh-bibs-and-tips-7"}
        )
        self.assertRedirects(response, "/accounts/reset/done/")
        self.assertTrue(self.client.login(email="reception@kowhai.test", password="fresh-bibs-and-tips-7"))

    def test_password_reset_for_an_unknown_email_sends_nothing_and_says_the_same(self):
        response = self.client.post("/accounts/password_reset/", {"email": "nobody@nowhere.test"})
        self.assertRedirects(response, "/accounts/password_reset/done/")
        self.assertEqual(len(mail.outbox), 0)

    def test_change_password(self):
        self.client.post("/accounts/signup/", self.signup_data)
        self.client.post("/accounts/logout/")
        self.client.post("/accounts/login/", {"username": "reception@kowhai.test", "password": self.signup_data["password"]})
        response = self.client.post(
            "/accounts/password_change/",
            {
                "old_password": self.signup_data["password"],
                "new_password1": "fresh-bibs-and-tips-7",
                "new_password2": "fresh-bibs-and-tips-7",
            },
        )
        self.assertRedirects(response, "/accounts/password_change/done/")
        self.assertTrue(self.client.login(email="reception@kowhai.test", password="fresh-bibs-and-tips-7"))


class TeamTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Kowhai Dental")
        cls.admin = User.objects.create_user("sandy@kowhai.test", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.assistant = User.objects.create_staff(cls.org, "Liz", "22")

    def setUp(self):
        self.client.force_login(self.admin)

    def test_add_staff_with_a_code(self):
        response = self.client.post("/accounts/team/add/", {"name": "Johanna", "pin": "11", "role": "assistant"})
        self.assertRedirects(response, "/accounts/team/")
        johanna = User.objects.get(organisation=self.org, pin="11")
        self.assertEqual(johanna.name, "Johanna")
        self.assertEqual(johanna.role, User.Role.ASSISTANT)
        self.assertIsNone(johanna.email)
        self.assertFalse(johanna.has_usable_password())

    def test_add_staff_as_an_admin_with_four_digits(self):
        self.client.post("/accounts/team/add/", {"name": "Owner", "pin": "5555", "role": "admin"})
        self.assertTrue(User.objects.get(organisation=self.org, pin="5555").is_org_admin)

    def test_an_admin_cannot_have_a_two_digit_code(self):
        response = self.client.post("/accounts/team/add/", {"name": "Owner", "pin": "55", "role": "admin"})
        self.assertContains(response, "A manager needs a 4-digit code")
        self.assertFalse(User.objects.filter(name="Owner").exists())

    def test_an_assistant_cannot_have_a_four_digit_code(self):
        response = self.client.post("/accounts/team/add/", {"name": "Someone", "pin": "1234", "role": "assistant"})
        self.assertContains(response, "An assistant uses a 2-digit code")
        self.assertFalse(User.objects.filter(name="Someone").exists())

    def test_the_database_enforces_code_length_by_role(self):
        for role, pin in [(User.Role.ADMIN, "12"), (User.Role.ASSISTANT, "1234")]:
            with self.subTest(role), self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.create_staff(self.org, "Wrong", pin, role)

    def test_a_code_can_only_be_used_once_per_practice(self):
        response = self.client.post("/accounts/team/add/", {"name": "Someone", "pin": "22", "role": "assistant"})
        self.assertContains(response, "Liz already uses 22")
        # Another practice can reuse it.
        User.objects.create_staff(Organisation.objects.create(name="Other"), "Other Liz", "22")

    def test_codes_are_two_digits(self):
        for pin in ["1", "123", "ab"]:
            with self.subTest(pin):
                response = self.client.post("/accounts/team/add/", {"name": "Someone", "pin": pin, "role": "assistant"})
                self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(name="Someone").exists())

    def test_team_page_shows_assistant_codes_but_not_manager_codes(self):
        User.objects.create_staff(self.org, "Owner", "5555", User.Role.ADMIN)
        content = self.client.get("/accounts/team/").content.decode()
        self.assertIn("22", content)
        self.assertNotIn("5555", content)

    def promote(self, **data):
        return self.client.post(f"/accounts/team/{self.assistant.pk}/role/", {"role": "admin", **data})

    def test_promoting_someone_asks_them_for_a_new_four_digit_code(self):
        page = self.client.get(f"/accounts/team/{self.assistant.pk}/role/?role=admin")
        self.assertContains(page, "New 4-digit code")
        self.assertContains(page, "Liz, choose your new code")
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.role, User.Role.ASSISTANT)  # nothing changes until they choose one

        response = self.promote(pin="7302", pin_again="7302")
        self.assertRedirects(response, "/accounts/team/")
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_org_admin)
        self.assertEqual(self.assistant.pin, "7302")

    def test_promotion_refuses_a_two_digit_code(self):
        self.assertEqual(self.promote(pin="73", pin_again="73").status_code, 200)
        self.assistant.refresh_from_db()
        self.assertEqual((self.assistant.role, self.assistant.pin), (User.Role.ASSISTANT, "22"))

    def test_promotion_needs_the_code_typed_twice_the_same(self):
        self.assertContains(self.promote(pin="7302", pin_again="7320"), "match")
        self.assistant.refresh_from_db()
        self.assertEqual(self.assistant.role, User.Role.ASSISTANT)

    def test_promotion_refuses_a_code_someone_else_has(self):
        User.objects.create_staff(self.org, "Owner", "5555", User.Role.ADMIN)
        self.assertContains(self.promote(pin="5555", pin_again="5555"), "Owner already uses 5555")

    def test_making_a_manager_an_assistant_asks_for_a_new_two_digit_code(self):
        owner = User.objects.create_staff(self.org, "Owner", "5555", User.Role.ADMIN)
        url = f"/accounts/team/{owner.pk}/role/"
        self.assertEqual(self.client.post(url, {"role": "assistant", "pin": "5555", "pin_again": "5555"}).status_code, 200)
        self.client.post(url, {"role": "assistant", "pin": "55", "pin_again": "55"})
        owner.refresh_from_db()
        self.assertEqual((owner.role, owner.pin), (User.Role.ASSISTANT, "55"))

    def test_someone_with_an_email_login_changes_role_in_one_tap(self):
        solo = User.objects.create_user("solo@kowhai.test", "pw", organisation=self.org)
        self.assertRedirects(self.client.post(f"/accounts/team/{solo.pk}/role/", {"role": "admin"}), "/accounts/team/")
        solo.refresh_from_db()
        self.assertTrue(solo.is_org_admin)

    def test_cannot_demote_the_last_admin(self):
        response = self.client.post(f"/accounts/team/{self.admin.pk}/role/", {"role": "assistant"}, follow=True)
        self.assertContains(response, "the only admin")
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ADMIN)

    def test_can_demote_an_admin_when_another_admin_remains(self):
        User.objects.create_user("other@kowhai.test", "pw", organisation=self.org, role=User.Role.ADMIN)
        self.client.post(f"/accounts/team/{self.admin.pk}/role/", {"role": "assistant"})
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, User.Role.ASSISTANT)

    def test_deactivate_and_reactivate(self):
        self.client.post(f"/accounts/team/{self.assistant.pk}/active/", {"active": "0"})
        self.assistant.refresh_from_db()
        self.assertFalse(self.assistant.is_active)

        self.client.post(f"/accounts/team/{self.assistant.pk}/active/", {"active": "1"})
        self.assistant.refresh_from_db()
        self.assertTrue(self.assistant.is_active)

    def test_cannot_deactivate_the_last_admin(self):
        self.client.post(f"/accounts/team/{self.admin.pk}/active/", {"active": "0"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_cannot_deactivate_yourself_even_with_another_admin_around(self):
        User.objects.create_user("other@kowhai.test", "pw", organisation=self.org, role=User.Role.ADMIN)
        response = self.client.post(f"/accounts/team/{self.admin.pk}/active/", {"active": "0"}, follow=True)
        self.assertContains(response, "deactivate yourself")
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)


class PracticeLoginTests(TestCase):
    """One email-and-password login per practice, then a code per person."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Discover Dental")
        cls.practice = User.objects.create_user(
            "reception@discover.test", "pw", organisation=cls.org, role=User.Role.ADMIN, is_practice_login=True
        )
        cls.sandy = User.objects.create_staff(cls.org, "Sandy", "0000", User.Role.ADMIN)
        cls.johanna = User.objects.create_staff(cls.org, "Johanna", "11")

    def open_device(self):
        self.client.post("/accounts/login/", {"username": "reception@discover.test", "password": "pw"})

    def enter_code(self, pin):
        return self.client.post("/accounts/code/", {"pin": pin})

    def signed_in_as(self):
        return int(self.client.session["_auth_user_id"])

    def test_the_practice_login_alone_only_reaches_the_code_pad_and_team(self):
        self.open_device()
        for url in ["/", "/log-usage/", "/reorder/", "/items/"]:
            with self.subTest(url):
                self.assertRedirects(self.client.get(url), "/accounts/code/")
        self.assertEqual(self.client.get("/accounts/code/").status_code, 200)
        self.assertEqual(self.client.get("/accounts/team/").status_code, 200)

    def test_a_code_signs_that_person_in(self):
        self.open_device()
        self.assertRedirects(self.enter_code("11"), "/")
        self.assertEqual(self.signed_in_as(), self.johanna.pk)
        self.assertContains(self.client.get("/"), "Johanna")

    def test_switching_person_needs_only_their_code(self):
        self.open_device()
        self.enter_code("11")
        self.assertEqual(self.client.get("/accounts/code/").status_code, 200)
        self.enter_code("0000")
        self.assertEqual(self.signed_in_as(), self.sandy.pk)
        self.assertEqual(self.client.get("/items/").status_code, 200)  # Sandy is an admin

    def test_a_wrong_code_says_so_and_changes_nothing(self):
        self.open_device()
        response = self.enter_code("99")
        self.assertContains(response, "match anyone")
        self.assertEqual(self.signed_in_as(), self.practice.pk)

    def test_a_deactivated_persons_code_does_not_work(self):
        User.objects.filter(pk=self.johanna.pk).update(is_active=False)
        self.open_device()
        self.assertContains(self.enter_code("11"), "match anyone")

    def test_a_code_never_reaches_another_practice(self):
        other = Organisation.objects.create(name="Other Dental")
        User.objects.create_staff(other, "Stranger", "44")
        self.open_device()
        self.assertContains(self.enter_code("44"), "match anyone")

    def test_changing_the_practice_password_signs_every_device_out(self):
        self.open_device()
        self.enter_code("11")
        self.practice.set_password("a-brand-new-password-9")
        self.practice.save()
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/accounts/login/"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_deactivating_the_practice_login_signs_every_device_out(self):
        self.open_device()
        self.enter_code("11")
        User.objects.filter(pk=self.practice.pk).update(is_active=False)
        self.assertTrue(self.client.get("/").url.startswith("/accounts/login/"))

    def test_a_staff_session_without_a_practice_login_is_signed_out(self):
        self.client.force_login(self.johanna)
        self.assertTrue(self.client.get("/").url.startswith("/accounts/login/"))

    def test_the_practice_login_can_make_someone_admin(self):
        self.open_device()
        self.client.post(f"/accounts/team/{self.johanna.pk}/role/", {"role": "admin", "pin": "1111", "pin_again": "1111"})
        self.johanna.refresh_from_db()
        self.assertTrue(self.johanna.is_org_admin)

    def test_an_admin_can_make_someone_admin(self):
        self.open_device()
        self.enter_code("0000")
        self.client.post(f"/accounts/team/{self.johanna.pk}/role/", {"role": "admin", "pin": "1111", "pin_again": "1111"})
        self.johanna.refresh_from_db()
        self.assertTrue(self.johanna.is_org_admin)

    def test_an_assistant_cannot_reach_the_team_page(self):
        self.open_device()
        self.enter_code("11")
        self.assertEqual(self.client.get("/accounts/team/").status_code, 403)

    def test_the_practice_login_can_always_demote_the_last_staff_admin(self):
        self.open_device()
        self.client.post(f"/accounts/team/{self.sandy.pk}/role/", {"role": "assistant", "pin": "33", "pin_again": "33"})
        self.sandy.refresh_from_db()
        self.assertEqual(self.sandy.role, User.Role.ASSISTANT)

    def test_the_practice_login_is_not_on_the_team_page_to_lock_out(self):
        self.open_device()
        self.enter_code("0000")
        for url in [f"/accounts/team/{self.practice.pk}/role/", f"/accounts/team/{self.practice.pk}/active/"]:
            with self.subTest(url):
                self.assertEqual(self.client.post(url, {"role": "assistant", "active": "0"}).status_code, 404)
        self.practice.refresh_from_db()
        self.assertTrue(self.practice.is_active)

    def test_someone_with_their_own_email_login_has_no_code_pad(self):
        solo = User.objects.create_user("solo@discover.test", "pw", organisation=self.org)
        self.client.force_login(solo)
        self.assertEqual(self.client.get("/accounts/code/").status_code, 403)

    def test_staff_have_no_email_so_the_unique_email_rule_allows_many(self):
        self.assertIsNone(self.sandy.email)
        self.assertEqual(str(self.sandy), "Sandy")
        self.sandy.full_clean()  # AbstractUser.clean() would otherwise turn None into a shared ""
        self.assertIsNone(self.sandy.email)
