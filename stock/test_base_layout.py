from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organisation, User
from stockroom.demo import DEMO_ORG

NAV_LINK = 'class="nav-link"'


class DesktopNavTests(TestCase):
    """base.html grows a second, `lg:`-only nav row in the header, alongside
    the phone's bottom tab bar (now `lg:hidden`) - see progress.md's
    "Desktop layout" note. Both exist in every response; only CSS picks
    which one shows, so this checks the row has the right links for each role."""

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo")
        org = Organisation.objects.get(name=DEMO_ORG)
        cls.practice = User.objects.get(organisation=org, is_practice_login=True)
        cls.admin = User.objects.get(organisation=org, name="Sofia")
        cls.assistant = User.objects.get(organisation=org, name="Emilio")

    def sign_in(self, staff):
        self.client.force_login(self.practice)
        self.client.post("/accounts/code/", {"pin": staff.pin})

    def test_admin_gets_every_page_directly_no_more_dropdown_needed(self):
        self.sign_in(self.admin)
        content = self.client.get("/").content.decode()
        self.assertEqual(content.count(NAV_LINK), 10)
        for label in ("Home", "Log usage", "Reorder list", "Deliveries",
                      "Stock", "Suppliers", "Spending", "Tax", "Activity", "Team"):
            self.assertIn(f">{label}</a>", content)
        # The phone's bottom tabs are still there too, hidden by lg:hidden not removed.
        self.assertIn('class="nav-tab"', content)

    def test_assistant_gets_only_the_pages_they_can_use(self):
        self.sign_in(self.assistant)
        content = self.client.get("/").content.decode()
        self.assertEqual(content.count(NAV_LINK), 4)
        self.assertNotIn(">Suppliers</a>", content)
        self.assertNotIn(">Team</a>", content)

    def test_the_current_page_is_marked(self):
        self.sign_in(self.admin)
        content = self.client.get("/reorder/").content.decode()
        self.assertIn('href="/reorder/" class="nav-link" aria-current="page"', content)

    def test_the_practice_login_alone_gets_no_nav_row(self):
        self.client.force_login(self.practice)
        content = self.client.get("/accounts/code/").content.decode()
        self.assertNotIn(NAV_LINK, content)
