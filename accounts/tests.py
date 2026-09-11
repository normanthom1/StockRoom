from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

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
