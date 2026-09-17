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
from django.urls import URLResolver, get_resolver, path, reverse

from accounts.decorators import admin_required
from accounts.models import Organisation, User
from stock.models import (
    CatalogueProduct,
    Invoice,
    InvoiceBatch,
    InvoiceLine,
    Item,
    ItemAlias,
    OrderLine,
    StockEvent,
    Supplier,
)


class Case(NamedTuple):
    url_name: str
    # make(org) creates an object owned by org; its pk goes in the URL as `pk`.
    make: Callable | None = None
    method: str = "get"


def make_member(org):
    """A plain assistant in org, for Cases that need some existing user's pk."""
    return User.objects.create_user(f"member-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)


def make_item(org):
    """An item in org, for Cases that need some existing item's pk."""
    supplier = Supplier.objects.create(organisation=org, name=f"Supplier {Supplier.objects.count()}")
    return Item.objects.create(organisation=org, name=f"Item {Item.objects.count()}", unit="box", supplier=supplier)


def make_stock_event(org):
    """A stock event in org, for Cases that need some existing event's pk."""
    item = make_item(org)
    user = User.objects.create_user(f"eventuser-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)
    return StockEvent.objects.create(organisation=org, item=item, user=user, kind="low")


def make_supplier(org):
    """A supplier in org, for Cases that need some existing supplier's pk."""
    return Supplier.objects.create(organisation=org, name=f"Supplier {Supplier.objects.count()}")


def any_catalogue_product(org):
    """The catalogue is shared, not practice data: any product will do."""
    return CatalogueProduct.objects.first()


def make_order_line(org):
    """An open order line in org, for Cases that need some existing order's pk."""
    item = make_item(org)
    user = User.objects.create_user(f"orderuser-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)
    return OrderLine.objects.create(organisation=org, item=item, qty=1, ordered_by=user)


def make_invoice(org):
    """An invoice in org needing a check (no supplier), for Cases that need some existing invoice's pk."""
    return Invoice.objects.create(organisation=org, supplier_name=f"Supplier {Invoice.objects.count()}",
                                  status=Invoice.Status.CONFLICT)


def make_invoice_line(org):
    """A line on an imported invoice in org, for Cases that need some existing invoice line's pk."""
    invoice = Invoice.objects.create(organisation=org, supplier=make_supplier(org), status=Invoice.Status.PARSED)
    return InvoiceLine.objects.create(organisation=org, invoice=invoice, description="Gloves", qty=1)


def make_batch(org):
    """An invoice batch in org, for Cases that need some existing batch's pk."""
    return InvoiceBatch.objects.create(organisation=org, created_by=make_member(org))


def make_alias(org):
    """A matched name in org, for Cases that need some existing alias's pk."""
    item = make_item(org)
    user = User.objects.create_user(f"aliasuser-{User.objects.count()}@{org.slug}.test", "pw", organisation=org)
    return ItemAlias.objects.create(organisation=org, item=item, key="glove", raw_name="glove", method="exact",
                                    confidence=1.0, source="import", created_by=user)


# Objects a practice owns. Another practice's user gets a 404 for each.
ORG_OBJECT_URLS: list[Case] = [
    Case("team_role", make=make_member, method="post"),
    Case("team_set_active", make=make_member, method="post"),
    Case("stock:item_detail", make=make_item),
    Case("stock:log_used_one", make=make_item, method="post"),
    Case("stock:log_running_low", make=make_item, method="post"),
    Case("stock:log_used_last", make=make_item, method="post"),
    Case("stock:log_undo", make=make_stock_event, method="post"),
    Case("stock:item_count_sheet", make=make_item),
    Case("stock:item_set_price", make=make_item, method="post"),
    Case("stock:item_set_order_size", make=make_item, method="post"),
    Case("stock:item_toggle_reorder", make=make_item, method="post"),
    Case("stock:item_switch_supplier", make=make_item, method="post"),
    Case("stock:item_other_suppliers", make=make_item, method="post"),
    Case("stock:catalogue_undo", make=make_item, method="post"),
    Case("stock:item_count_save", make=make_item, method="post"),
    Case("stock:supplier_row", make=make_supplier),
    Case("stock:supplier_edit", make=make_supplier),
    Case("stock:supplier_update", make=make_supplier, method="post"),
    Case("stock:supplier_lead_days", make=make_supplier, method="post"),
    Case("stock:supplier_archive", make=make_supplier, method="post"),
    Case("stock:supplier_unarchive", make=make_supplier, method="post"),
    Case("stock:item_view_row", make=make_item),
    Case("stock:item_edit_row", make=make_item),
    Case("stock:item_update_row", make=make_item, method="post"),
    Case("stock:item_archive", make=make_item, method="post"),
    Case("stock:item_unarchive", make=make_item, method="post"),
    Case("stock:reorder_mark_ordered", make=make_item, method="post"),
    Case("stock:reorder_mark_supplier_ordered", make=make_supplier, method="post"),
    Case("stock:reorder_undo", make=make_order_line, method="post"),
    Case("stock:reorder_add", make=make_item, method="post"),
    Case("stock:reorder_add_undo", make=make_item, method="post"),
    Case("stock:reorder_decline", make=make_item, method="post"),
    Case("stock:delivery_submit", make=make_supplier, method="post"),
    Case("stock:supplier_apply_lead_days", make=make_supplier, method="post"),
    Case("stock:merge_undo", make=make_alias, method="post"),
    Case("stock:invoice_detail", make=make_invoice),
    Case("stock:invoice_check", make=make_invoice),
    Case("stock:invoice_batch", make=make_batch),
    Case("assistant:invoice_batch_resume", make=make_batch, method="post"),
    Case("stock:invoice_line_receive", make=make_invoice_line, method="post"),
]

# Manager-only views. Assistants get a 403 for each.
ADMIN_ONLY_URLS: list[Case] = [
    Case("team"),
    Case("team_add", method="post"),
    Case("team_role", make=make_member, method="post"),
    Case("team_set_active", make=make_member, method="post"),
    Case("stock:items"),
    Case("stock:suppliers"),
    Case("stock:spending"),
    Case("stock:item_set_price", make=make_item, method="post"),
    Case("stock:item_set_order_size", make=make_item, method="post"),
    Case("stock:item_toggle_reorder", make=make_item, method="post"),
    Case("stock:item_switch_supplier", make=make_item, method="post"),
    Case("stock:item_other_suppliers", make=make_item, method="post"),
    Case("stock:catalogue"),
    Case("stock:catalogue_sheet", make=any_catalogue_product),
    Case("stock:catalogue_add", make=any_catalogue_product, method="post"),
    Case("stock:catalogue_undo", make=make_item, method="post"),
    Case("stock:stocktake_step"),
    Case("stock:stocktake_save", method="post"),
    Case("stock:supplier_add", method="post"),
    Case("stock:supplier_row", make=make_supplier),
    Case("stock:supplier_edit", make=make_supplier),
    Case("stock:supplier_update", make=make_supplier, method="post"),
    Case("stock:supplier_lead_days", make=make_supplier, method="post"),
    Case("stock:supplier_archive", make=make_supplier, method="post"),
    Case("stock:supplier_unarchive", make=make_supplier, method="post"),
    Case("stock:item_add", method="post"),
    Case("stock:item_view_row", make=make_item),
    Case("stock:item_edit_row", make=make_item),
    Case("stock:item_update_row", make=make_item, method="post"),
    Case("stock:item_archive", make=make_item, method="post"),
    Case("stock:item_unarchive", make=make_item, method="post"),
    Case("stock:item_import"),
    Case("stock:item_import_preview", method="post"),
    Case("stock:item_import_confirm", method="post"),
    Case("stock:merges"),
    Case("stock:merge_undo", make=make_alias, method="post"),
    Case("stock:reorder_mark_ordered", make=make_item, method="post"),
    Case("stock:reorder_mark_supplier_ordered", make=make_supplier, method="post"),
    Case("stock:reorder_undo", make=make_order_line, method="post"),
    Case("stock:reorder_undo_batch", method="post"),
    Case("stock:reorder_decline", make=make_item, method="post"),
    Case("stock:supplier_apply_lead_days", make=make_supplier, method="post"),
    Case("stock:activity_log"),
    Case("stock:export_items"),
    Case("stock:export_stock_events"),
    Case("stock:export_order_lines"),
    Case("assistant:import_with_ai", method="post"),
    Case("stock:invoice_upload"),
    Case("stock:invoice_confirm", method="post"),
    Case("stock:invoice_detail", make=make_invoice),
    Case("stock:invoice_check", make=make_invoice),
    Case("stock:invoice_batch", make=make_batch),
    Case("assistant:invoice_upload", method="post"),
    Case("assistant:invoice_batch_upload", method="post"),
    Case("assistant:invoice_batch_resume", make=make_batch, method="post"),
    Case("stock:invoice_order_search"),
    Case("stock:invoice_line_receive", make=make_invoice_line, method="post"),
]

# Pages an assistant can open. None of them may show a price.
ASSISTANT_PAGES: list[Case] = [
    Case("stock:home"),
    Case("stock:log_usage"),
    Case("stock:reorder_list"),
    Case("stock:deliveries"),
    Case("stock:item_detail", make=make_item),
    Case("stock:item_count_sheet", make=make_item),
    Case("assistant:ask"),  # and what's sent to Gemini for an assistant has no prices (assistant.tests)
]

# URLs that show no practice data at all, so none of the checks above apply.
NO_PRACTICE_DATA: dict[str, str] = {
    "healthz": "Railway's healthcheck; says 'ok'",
    "service_worker": "the same JavaScript for everyone",
    "offline": "rendered without the request, so it can't hold anyone's data",
    "login": "public",
    "demo_login": "public; 404s unless DEMO_MODE, and only reaches the fixed demo practice",
    "logout": "ends your own session",
    "signup": "public; creates a new practice",
    "enter_code": "codes only match staff of the practice login that opened the session (accounts.tests.PracticeLoginTests)",
    "password_change": "your own password",
    "password_change_done": "your own password",
    "password_reset": "public",
    "password_reset_done": "public",
    "password_reset_confirm": "public; the token names the user",
    "password_reset_complete": "public",
}

PRICE = re.compile(r"\$\s?\d")  # "$8.50", "$ 1,489.20"


def url_names(patterns=None, namespace=""):
    """Every URL name in the project, namespaced. Django's admin is left out:
    it's superusers-only as a whole (test_django_admin_is_superusers_only)."""
    for pattern in get_resolver().url_patterns if patterns is None else patterns:
        if isinstance(pattern, URLResolver):
            if pattern.namespace == "admin":
                continue
            inner = f"{namespace}{pattern.namespace}:" if pattern.namespace else namespace
            yield from url_names(pattern.url_patterns, inner)
        else:
            yield f"{namespace}{pattern.name}" if pattern.name else f"<unnamed: {pattern.pattern}>"


def unregistered_urls():
    registered = {case.url_name for case in ORG_OBJECT_URLS + ADMIN_ONLY_URLS + ASSISTANT_PAGES} | set(NO_PRACTICE_DATA)
    return sorted(set(url_names()) - registered)


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


@override_settings(AI_API_KEY="test-key")  # otherwise the AI views 404 for everyone
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

    def test_every_url_is_registered_with_the_isolation_checks(self):
        self.assertEqual(unregistered_urls(), [], "Register these in ORG_OBJECT_URLS, ADMIN_ONLY_URLS, "
                         "ASSISTANT_PAGES or NO_PRACTICE_DATA (see docs/org-scoped-views.md)")

    def test_no_practice_data_list_has_no_stale_names(self):
        self.assertLessEqual(set(NO_PRACTICE_DATA), set(url_names()))


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

    def test_an_unregistered_url_is_caught(self):
        # This module's throwaway views aren't registered anywhere.
        self.assertIn("leaky", unregistered_urls())

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
        url = reverse("admin:index")
        self.client.force_login(self.admin_a)
        self.assertEqual(self.client.get(url).status_code, 302)
        staff = User.objects.create_user("staff@a.test", "pw", organisation=self.org_a, is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.platform)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_django_admin_is_not_at_the_default_url(self):
        self.assertNotEqual(reverse("admin:index"), "/admin/")
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_healthz_stays_public_for_railway(self):
        self.assertEqual(self.client.get("/healthz").status_code, 200)
