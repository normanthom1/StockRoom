"""Tenant isolation: one practice never sees another's data, and assistants
never reach manager-only pages or see prices.

Every view that shows or changes practice data gets registered below, in the
same PR that adds it. See docs/org-scoped-views.md.
"""

import re
from collections.abc import Callable
from typing import NamedTuple

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.test import TestCase, override_settings
from django.urls import path, reverse

from accounts.decorators import admin_required
from accounts.models import Organisation, User


class Case(NamedTuple):
    url_name: str
    # make(org) creates an object owned by org; its pk goes in the URL as `pk`.
    make: Callable | None = None
    method: str = "get"


def make_member(org):
    """A plain assistant in org, for Cases that need some existing user's pk."""
    return User.objects.create_user(f"member-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)


# Objects a practice owns. Another practice's user gets a 404 for each.
ORG_OBJECT_URLS: list[Case] = [
    Case("team_role", make=make_member, method="post"),
    Case("team_set_active", make=make_member, method="post"),
    Case("team_reset_link", make=make_member, method="post"),
]

# Manager-only views. Assistants get a 403 for each.
ADMIN_ONLY_URLS: list[Case] = [
    Case("team"),
    Case("team_invite", method="post"),
    Case("team_role", make=make_member, method="post"),
    Case("team_set_active", make=make_member, method="post"),
    Case("team_reset_link", make=make_member, method="post"),
]

# Pages an assistant can open. None of them may show a price.
ASSISTANT_PAGES: list[Case] = [
    Case("stock:home"),
]

PRICE = re.compile(r"\$\s?\d")  # "$8.50", "$ 1,489.20"


class IsolationChecks:
    """The three checks, shared by the real suite and the self-tests below."""

    def url_for(self, case, org):
        kwargs = {"pk": case.make(org).pk} if case.make else {}
        return reverse(case.url_name, kwargs=kwargs)

    def request(self, case, url):
        return getattr(self.client, case.method)(url)

    def assert_other_practice_gets_404(self, case):
        self.client.force_login(self.admin_b)  # B's most privileged user
        response = self.request(case, self.url_for(case, self.org_a))
        self.assertEqual(response.status_code, 404, f"{case.url_name} leaks practice A's object to practice B")

    def assert_assistant_gets_403(self, case):
        self.client.force_login(self.assistant_a)
        response = self.request(case, self.url_for(case, self.org_a))
        self.assertEqual(response.status_code, 403, f"{case.url_name} lets an assistant in")

    def assert_assistant_sees_no_prices(self, case):
        self.client.force_login(self.assistant_a)
        response = self.request(case, self.url_for(case, self.org_a))
        self.assertEqual(response.status_code, 200)
        self.assertNotRegex(response.content.decode(), PRICE, f"{case.url_name} shows an assistant a price")


class TwoPractices(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organisation.objects.create(name="Practice A")
        cls.org_b = Organisation.objects.create(name="Practice B")
        cls.admin_a = User.objects.create_user("admin@a.test", "pw", organisation=cls.org_a, role=User.Role.ADMIN)
        cls.assistant_a = User.objects.create_user("assistant@a.test", "pw", organisation=cls.org_a)
        cls.admin_b = User.objects.create_user("admin@b.test", "pw", organisation=cls.org_b, role=User.Role.ADMIN)
        cls.platform = User.objects.create_superuser("platform@stockroom.test", "pw")


class IsolationSuite(IsolationChecks, TwoPractices):
    def test_other_practices_objects_are_404(self):
        for case in ORG_OBJECT_URLS:
            with self.subTest(case.url_name):
                self.assert_other_practice_gets_404(case)

    def test_assistants_get_403_on_admin_views(self):
        for case in ADMIN_ONLY_URLS:
            with self.subTest(case.url_name):
                self.assert_assistant_gets_403(case)

    def test_assistant_pages_show_no_prices(self):
        for case in ASSISTANT_PAGES:
            with self.subTest(case.url_name):
                self.assert_assistant_sees_no_prices(case)


# Throwaway views that exist only to prove the checks catch real mistakes.


def leaky_member(request, pk):
    return HttpResponse(User.objects.get(pk=pk).email)


def scoped_member(request, pk):
    return HttpResponse(get_object_or_404(User.objects.for_org(request.user.organisation), pk=pk).email)


@admin_required
def manager_page(request):
    return HttpResponse("team settings")


def priced_page(request):
    return HttpResponse("Gloves $8.50 a box")


@login_not_required
def public_page(request):
    return HttpResponse("hello")


urlpatterns = [
    path("leaky/<int:pk>/", leaky_member, name="leaky"),
    path("scoped/<int:pk>/", scoped_member, name="scoped"),
    path("manager/", manager_page, name="manager"),
    path("priced/", priced_page, name="priced"),
    path("public/", public_page, name="public"),
]


def a_member(org):
    return User.objects.create_user(f"member-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)


@override_settings(ROOT_URLCONF=__name__)
class ChecksCatchMistakes(IsolationChecks, TwoPractices):
    def test_cross_practice_lookup_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_other_practice_gets_404(Case("leaky", a_member))
        self.assert_other_practice_gets_404(Case("scoped", a_member))

    def test_admin_only_view_is_enforced(self):
        self.assert_assistant_gets_403(Case("manager"))
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(reverse("manager")).status_code, 200)
        # Superusers belong to no practice, so they aren't anyone's practice admin.
        self.client.force_login(self.platform)
        self.assertEqual(self.client.get(reverse("manager")).status_code, 403)

    def test_prices_are_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_assistant_sees_no_prices(Case("priced"))

    def test_login_is_required_by_default(self):
        response = self.client.get(reverse("manager"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))
        self.assertEqual(self.client.get(reverse("public")).status_code, 200)


class ScopingPrimitives(TwoPractices):
    def test_for_org_none_matches_nothing(self):
        # Superusers have organisation=None; a stray None must not list them.
        self.assertFalse(User.objects.for_org(None).exists())
        self.assertEqual(set(User.objects.for_org(self.org_a)), {self.admin_a, self.assistant_a})

    def test_django_admin_is_superusers_only(self):
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        staff = User.objects.create_user("staff@a.test", "pw", organisation=self.org_a, is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.client.force_login(self.platform)
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_healthz_stays_public_for_railway(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
