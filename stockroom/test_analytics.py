"""UX-05. StockRoom had no instrumentation, so nobody could tell whether a UX
change worked. These check the events carry what the metrics need, and carry
nothing personal."""

import json
from contextlib import redirect_stdout
from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import Organisation, User
from stock.models import Invoice, Item, StockEvent, Supplier
from stockroom import analytics


class TrackTests(TestCase):
    def test_an_event_is_one_json_line(self):
        with self.assertLogs("stockroom.analytics", "INFO") as logs:
            analytics.track("setup_completed", org=7, items=44)

        payload = json.loads(logs.output[0].partition(":")[2].partition(":")[2])
        self.assertEqual(payload, {"event": "setup_completed", "org": 7, "items": 44})

    def test_an_unknown_event_is_a_typo_and_says_so(self):
        with self.assertRaises(ValueError):
            analytics.track("setup_finished")  # not in EVENTS

    def test_a_broken_field_never_breaks_the_caller(self):
        class Unserialisable:
            def __str__(self):
                raise RuntimeError("nope")

        with self.assertLogs("stockroom.analytics", "ERROR"):
            analytics.track("setup_completed", org=1, thing=Unserialisable())


class FlowEventTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.admin = User.objects.create_user("sandy@example.com", "pw", organisation=self.org,
                                              role=User.Role.ADMIN)
        self.client.force_login(self.admin)

    def test_opening_the_checklist_records_how_far_through_they_are(self):
        with self.assertLogs("stockroom.analytics", "INFO") as logs:
            self.client.get("/setup/")

        events = [json.loads(line.partition(":")[2].partition(":")[2]) for line in logs.output]
        opened = next(e for e in events if e["event"] == "setup_step_opened")
        self.assertEqual((opened["org"], opened["done"]), (self.org.pk, 0))

    def test_an_import_records_how_it_landed(self):
        from stock.invoices import ingest, match_lines, parse_invoice

        Supplier.objects.create(organisation=self.org, name="Henry Schein")
        csv = b"vendor,description,qty,unit\nHenry Schein,Gloves,10,8.50\n"
        invoice, lines = parse_invoice(self.org, csv, "text/csv", "inv.csv")
        match_lines(invoice, lines)

        with self.assertLogs("stockroom.analytics", "INFO") as logs:
            ingest(invoice, lines, self.admin)

        event = json.loads(logs.output[0].partition(":")[2].partition(":")[2])
        self.assertEqual(event["event"], "invoice_imported")
        self.assertEqual((event["org"], event["lines"]), (self.org.pk, 1))

    def test_an_invoice_that_needs_checking_gets_its_own_event(self):
        from stock.invoices import ingest, match_lines, parse_invoice

        # No supplier of the practice's, so nothing can be received off it.
        csv = b"vendor,description,qty,unit\nNobody Ltd,Gloves,10,8.50\n"
        invoice, lines = parse_invoice(self.org, csv, "text/csv", "inv.csv")
        match_lines(invoice, lines)

        with self.assertLogs("stockroom.analytics", "INFO") as logs:
            ingest(invoice, lines, self.admin)

        events = [json.loads(line.partition(":")[2].partition(":")[2]) for line in logs.output]
        needs = next(e for e in events if e["event"] == "invoice_needs_checking")
        self.assertTrue(needs["no_supplier"])

    def test_no_event_carries_a_name_an_email_or_a_price(self):
        from stock.invoices import ingest, match_lines, parse_invoice

        Supplier.objects.create(organisation=self.org, name="Henry Schein")
        csv = b"vendor,description,qty,unit\nHenry Schein,Gloves,10,8.50\n"
        invoice, lines = parse_invoice(self.org, csv, "text/csv", "inv.csv")
        match_lines(invoice, lines)

        with self.assertLogs("stockroom.analytics", "INFO") as logs:
            self.client.get("/setup/")
            ingest(invoice, lines, self.admin)

        blob = " ".join(logs.output)
        for secret in ("Henry Schein", "sandy@example.com", "Test Dental", "Gloves", "8.50"):
            self.assertNotIn(secret, blob)


class MetricsCommandTests(TestCase):
    def test_it_reports_the_setup_completion_rate(self):
        done = Organisation.objects.create(name="Finished Dental")
        Organisation.objects.create(name="Stalled Dental")
        user = User.objects.create_user("a@b.test", "pw", organisation=done, role=User.Role.ADMIN)
        supplier = Supplier.objects.create(organisation=done, name="Henry Schein")
        item = Item.objects.create(organisation=done, name="Gloves", unit="box", supplier=supplier)
        StockEvent.objects.create(organisation=done, item=item, user=user, kind="count", qty=3)
        Invoice.objects.create(organisation=done, supplier_name="Nobody", issued_on=date(2026, 9, 18),
                               status=Invoice.Status.CONFLICT)

        out = StringIO()
        with redirect_stdout(out):
            call_command("ux_metrics", "--days", "3650")
        report = out.getvalue()

        self.assertIn("Setup completion rate: 50%", report)
        self.assertIn("1 of 2 reached a stock list", report)
        self.assertIn("needing checking", report)

    def test_it_says_so_rather_than_dividing_by_zero(self):
        out = StringIO()
        with redirect_stdout(out):
            call_command("ux_metrics", "--days", "1")
        self.assertIn("Nothing to measure yet.", out.getvalue())
