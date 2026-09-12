"""Cheap, no-browser regression guards for accessibility fixes made in #34.
The real check is scripts/axe_check.py (needs Playwright + axe-core, not run
in CI); these just stop the easy regressions from creeping back in silently.
"""

from django.test import TestCase

from accounts.models import Organisation, User


class ViewportTests(TestCase):
    def test_viewport_meta_does_not_block_pinch_zoom(self):
        content = self.client.get("/accounts/login/").content.decode()
        self.assertIn('name="viewport"', content)
        self.assertNotIn("maximum-scale", content)
        self.assertNotIn("user-scalable=no", content)


class ActivityFilterLabelsTests(TestCase):
    def test_filter_selects_have_accessible_names(self):
        org = Organisation.objects.create(name="Test Dental")
        admin = User.objects.create_user("sandy@example.com", "pw", organisation=org, role=User.Role.ADMIN)
        self.client.force_login(admin)
        content = self.client.get("/activity/").content.decode()
        self.assertIn('aria-label="Filter by item"', content)
        self.assertIn('aria-label="Filter by person"', content)
