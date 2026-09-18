from collections import Counter

import click
from django.apps import apps
from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organisation, User
from stockroom.demo import DEMO_ORG

from .forecast import Status, forecast
from .invoices import ingest, match_lines, parse_invoice
from .models import Invoice, InvoiceBatch, InvoiceLine, Item


def seeded_statuses():
    org = Organisation.objects.get(name=DEMO_ORG)
    counts = Counter()
    for item in Item.objects.filter(organisation=org):
        open_orders = [o for o in item.order_lines.all() if o.is_open]
        f = forecast(list(item.events.all()), open_orders, lead_days=item.supplier.lead_days)
        counts[f.status] += 1
    return org, counts


class SeedDemoTests(TestCase):
    def test_creates_the_demo_shape(self):
        call_command("seed_demo")
        org, counts = seeded_statuses()

        self.assertEqual(Item.objects.filter(organisation=org).count(), 44)
        self.assertEqual(counts[Status.OUT], 1)
        self.assertEqual(counts[Status.ORDER_NOW], 2)
        self.assertGreaterEqual(counts[Status.ORDER_THIS_WEEK], 1)
        practice = User.objects.get(organisation=org, is_practice_login=True)
        self.assertEqual(practice.email, "reception@nzdentist.co.nz")
        self.assertTrue(practice.check_password("password"))
        codes = dict(User.objects.filter(organisation=org, pin__isnull=False).values_list("name", "pin"))
        self.assertEqual(codes, {"Sofia": "0000", "Johanna": "11", "Emilio": "22", "Practice Owner": "5555"})

    def test_is_deterministic(self):
        call_command("seed_demo")
        _, first = seeded_statuses()
        call_command("seed_demo", "--reset")
        _, second = seeded_statuses()
        self.assertEqual(first, second)

    def test_composite_has_low_confidence_from_short_history(self):
        call_command("seed_demo")
        org, _ = seeded_statuses()
        composite = Item.objects.get(organisation=org, name="Composite, A2 syringes")
        f = forecast(list(composite.events.all()), [], lead_days=composite.supplier.lead_days)
        self.assertEqual(f.confidence, "low")

    def test_prophy_spike_week_is_excluded(self):
        call_command("seed_demo")
        org, _ = seeded_statuses()
        prophy = Item.objects.get(organisation=org, name="Prophy paste cups")
        f = forecast(list(prophy.events.all()), [], lead_days=prophy.supplier.lead_days)
        self.assertEqual(f.excluded_weeks, 1)

    def test_refuses_to_run_twice_without_reset(self):
        call_command("seed_demo")
        with self.assertRaises(click.ClickException):
            call_command("seed_demo")

    def test_reset_works_after_a_visitor_has_imported_invoices(self):
        call_command("seed_demo")
        org = Organisation.objects.get(name=DEMO_ORG)
        sofia = User.objects.get(organisation=org, name="Sofia")
        csv = b"vendor,description,qty,unit\nHenry Schein,Rubber dam sheets,10,1.00\n"
        for force_reason in ("", "Something else"):  # received against an order, then imported anyway
            invoice, lines = parse_invoice(org, csv, "text/csv")
            match_lines(invoice, lines)
            ingest(invoice, lines, sofia, force_reason)
        self.assertTrue(InvoiceLine.objects.filter(organisation=org, order_line__isnull=False).exists())
        InvoiceBatch.objects.create(organisation=org, created_by=sofia)

        call_command("seed_demo", "--reset")
        self.assertFalse(Invoice.objects.exists())

    def test_reset_only_touches_the_demo_organisation(self):
        other = Organisation.objects.create(name="Someone Else's Practice")
        User.objects.create_user("them@example.com", "pw", organisation=other)
        call_command("seed_demo")
        call_command("seed_demo", "--reset")
        self.assertTrue(Organisation.objects.filter(pk=other.pk).exists())
        self.assertEqual(User.objects.filter(organisation=other).count(), 1)


class DeleteOldDemoMigrationTests(TestCase):
    def test_removes_demo_dental_and_its_accounts_but_nothing_else(self):
        from importlib import import_module

        from django.apps import apps

        from .models import DemoResetState, StockEvent, Supplier

        old = Organisation.objects.create(name="Demo Dental")
        sandy = User.objects.create_user("sandy@demodental.test", "DemoPass123", organisation=old, role=User.Role.ADMIN)
        supplier = Supplier.objects.create(organisation=old, name="Henry Schein")
        item = Item.objects.create(organisation=old, name="Gloves", unit="box", supplier=supplier)
        StockEvent.objects.create(organisation=old, item=item, user=sandy, kind="low")
        other = Organisation.objects.create(name="Someone Else's Practice")
        User.objects.create_user("them@example.com", "pw", organisation=other)
        DemoResetState.objects.create(pk=1, date="2026-01-01")

        import_module("stock.migrations.0004_delete_old_demo_dental").delete_old_demo(apps, None)

        self.assertFalse(Organisation.objects.filter(name="Demo Dental").exists())
        self.assertFalse(User.objects.filter(email="sandy@demodental.test").exists())
        self.assertFalse(Item.objects.filter(name="Gloves").exists())
        self.assertTrue(User.objects.filter(email="them@example.com").exists())
        self.assertFalse(DemoResetState.objects.exists())  # so the demo re-seeds on its next request


class ResetAfterRealUseTests(TestCase):
    """--reset deletes the demo practice in dependency order, by hand, because a
    plain cascade can't resolve the PROTECTs between these models. That list was
    written before invoices kept the original file as a tax record, and nothing
    told it when InvoiceDocument arrived: reset then failed for anyone who had
    uploaded an invoice to the demo, which is most of the point of the demo."""

    def uploaded_invoice(self, org):
        """An invoice with its file kept, the way a real upload leaves one."""
        from django.utils import timezone

        from .models import InvoiceDocument, Supplier

        user = User.objects.get(organisation=org, is_practice_login=True)
        supplier = Supplier.objects.for_org(org).first()
        invoice = Invoice.objects.create(organisation=org, supplier=supplier, supplier_name=supplier.name,
                                         invoice_number="INV-1", totals_ok=True)
        InvoiceDocument.objects.create(organisation=org, invoice=invoice, filename="INV-1.pdf",
                                       content_type="application/pdf", data=b"%PDF", byte_size=4,
                                       checksum="abc", uploaded_by=user,
                                       retain_until=timezone.localdate())
        return invoice

    def test_reset_works_after_an_invoice_has_been_uploaded(self):
        call_command("seed_demo")
        org = Organisation.objects.get(name=DEMO_ORG)
        self.uploaded_invoice(org)

        call_command("seed_demo", "--reset")

        self.assertEqual(Organisation.objects.filter(name=DEMO_ORG).count(), 1)
        self.assertFalse(Invoice.objects.filter(invoice_number="INV-1").exists())

    def test_nothing_the_demo_owns_survives_a_reset(self):
        """Rather than listing models by hand here too: whatever rows the demo
        org owns before a reset, none of them may still point at it after. This
        fails the next time a model is added to the demo and left out of the
        deletion order, which is how InvoiceDocument got missed."""
        from accounts.models import OrgOwned

        call_command("seed_demo")
        org = Organisation.objects.get(name=DEMO_ORG)
        self.uploaded_invoice(org)
        owned = [m for m in apps.get_models() if issubclass(m, OrgOwned) and not m._meta.abstract]
        before = {m.__name__ for m in owned if m.objects.filter(organisation=org).exists()}
        self.assertIn("InvoiceDocument", before)  # the test is only worth anything if this is here

        call_command("seed_demo", "--reset")

        stale = {m.__name__ for m in owned if m.objects.filter(organisation_id=org.pk).exists()}
        self.assertEqual(stale, set(), "rows left pointing at the deleted demo organisation")
