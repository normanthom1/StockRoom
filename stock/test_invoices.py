import hashlib
from collections import Counter
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Organisation, User
from assistant import gemini

from . import invoices
from .forecast import on_hand
from .invoices import ingest, match_lines, parse_invoice, run_batch, save_invoice
from .models import (
    Invoice,
    InvoiceBatch,
    InvoiceBatchFile,
    Item,
    ItemAlias,
    OrderLine,
    StockEvent,
    Supplier,
)

# A Henry Schein invoice as Gemini reads it, with every kind of line that isn't a product.
AI_REPLY = {
    "invoice_date": "2026-09-14",
    "vendor": "Henry Schein",
    "po_number": "PO-1042",
    "lines": [
        {"sku": "HS-GLV-M", "description": "Nitrile gloves, size M", "qty": 10, "unit": 8.5, "line_total": 85},
        {"sku": "HS-BIB", "description": "Patient bibs", "qty": 2, "unit": 24.95, "line_total": 49.9},
        {"description": "Freight", "qty": 1, "unit": 12, "line_total": 12},
        {"description": "Discount 5%", "line_total": -6.74},
        {"description": "Subtotal", "line_total": 140.16},
        {"description": "GST 15%", "line_total": 21.02},
        {"description": "Total", "line_total": 161.18},
    ],
}

CSV = b"""\xef\xbb\xbfinvoice_date,vendor,po_number,sku,description,qty,unit,line_total
14/09/2026,henry schein,PO-1042,HS-GLV-M,"Nitrile gloves, size M",10,$8.50,$85.00
,,,HS-BIB,Patient bibs,2,24.95,49.90
,,,FREIGHT,Freight,1,12.00,12.00
,,,,Discount,,,-6.74
,,,,Subtotal,,,140.16
,,,,GST,,,21.02
,,,,Total,,,161.18
"""


class ParseInvoiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein")

    def assertParsed(self, invoice, lines):
        self.assertEqual(invoice.issued_on, date(2026, 9, 14))
        self.assertEqual(invoice.supplier, self.supplier)
        self.assertEqual(invoice.order_ref, "PO-1042")
        self.assertTrue(invoice.totals_ok)
        self.assertEqual(invoice.status, Invoice.Status.PARSED)
        self.assertEqual([(l.sku, l.qty, l.unit_price, l.line_total) for l in lines], [
            ("HS-GLV-M", 10, Decimal("8.50"), Decimal("85.00")),
            ("HS-BIB", 2, Decimal("24.95"), Decimal("49.90")),
        ])

    def test_pdf_and_photo_are_read_by_gemini_without_the_extra_lines(self):
        for mime_type in ("application/pdf", "image/jpeg"):
            with self.subTest(mime_type), mock.patch("assistant.gemini.generate", return_value=AI_REPLY) as generate:
                invoice, lines = parse_invoice(self.org, b"file bytes", mime_type)
            self.assertEqual(generate.call_args.kwargs["attachment"], (mime_type, b"file bytes"))
            self.assertIn("Henry Schein", generate.call_args.args[0])
            self.assertParsed(invoice, lines)

    def test_csv_is_read_without_gemini(self):
        with mock.patch("assistant.gemini.generate") as generate:
            invoice, lines = parse_invoice(self.org, CSV, "text/csv")
        generate.assert_not_called()
        self.assertParsed(invoice, lines)

    def test_gemini_labels_decide_what_is_a_product_not_the_wording(self):
        """The two cases the word match gets wrong: a product whose name starts
        with "Total", and freight carrying a product code (issue #106)."""
        reply = {**AI_REPLY, "lines": [
            {"kind": "product", "sku": "", "description": "Total Etch", "qty": 1, "unit": 32.0, "line_total": 32.0},
            {"kind": "freight", "sku": "90001", "description": "Freight", "qty": 1, "unit": 12.0, "line_total": 12.0},
            {"kind": "gst", "description": "GST 15%", "line_total": 6.6},
        ]}
        with mock.patch("assistant.gemini.generate", return_value=reply):
            _, lines = parse_invoice(self.org, b"x", "application/pdf")

        self.assertEqual([line.description for line in lines], ["Total Etch"])

    def test_the_schema_and_prompt_ask_for_a_kind(self):
        with mock.patch("assistant.gemini.generate", return_value=AI_REPLY) as generate:
            parse_invoice(self.org, b"x", "application/pdf")
        schema = generate.call_args.kwargs["schema"]
        self.assertEqual(schema["properties"]["lines"]["items"]["properties"]["kind"]["enum"], list(invoices.LINE_KINDS))
        self.assertIn("kind:", generate.call_args.args[0])

    def test_a_csv_kind_column_is_used_and_needs_no_gemini_call(self):
        csv = (b"sku,description,qty,unit,line_total,kind\n"
               b",Total Etch,1,32.00,32.00,product\n"
               b"90001,Freight,1,12.00,12.00,freight\n")
        with mock.patch("assistant.gemini.generate") as generate:
            _, lines = parse_invoice(self.org, csv, "text/csv")
        generate.assert_not_called()
        self.assertEqual([line.description for line in lines], ["Total Etch"])

    def test_a_csv_without_a_kind_column_falls_back_to_the_word_match(self):
        csv = (b"sku,description,qty,unit,line_total\n"
               b"HS-GLV-M,Nitrile gloves,1,8.50,8.50\n"
               b",Freight,1,12.00,12.00\n")
        _, lines = parse_invoice(self.org, csv, "text/csv")
        self.assertEqual([line.description for line in lines], ["Nitrile gloves"])

    def test_an_unlabelled_line_falls_back_to_the_word_match(self):
        """Gemini leaving kind off mustn't let GST through as a product."""
        reply = {**AI_REPLY, "lines": [
            {"sku": "HS-GLV-M", "description": "Nitrile gloves, size M", "qty": 1, "unit": 8.5, "line_total": 8.5},
            {"description": "GST 15%", "line_total": 1.28},
        ]}
        with mock.patch("assistant.gemini.generate", return_value=reply):
            _, lines = parse_invoice(self.org, b"x", "application/pdf")

        self.assertEqual([line.description for line in lines], ["Nitrile gloves, size M"])

    def test_a_line_that_doesnt_add_up_is_a_conflict(self):
        reply = {**AI_REPLY, "lines": [{"sku": "HS-BIB", "description": "Patient bibs", "qty": 2, "unit": 24.95,
                                        "line_total": 49.92}]}
        with mock.patch("assistant.gemini.generate", return_value=reply):
            invoice, _ = parse_invoice(self.org, b"x", "application/pdf")
        self.assertFalse(invoice.totals_ok)
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)

    def test_rounding_within_a_cent_adds_up(self):
        csv = b"sku,description,qty,unit,line_total\nHS-TIP,Suction tips,3,0.333,1.00\n"
        invoice, _ = parse_invoice(self.org, csv, "text/csv")
        self.assertTrue(invoice.totals_ok)

    def test_missing_price_or_no_lines_is_a_conflict(self):
        csv = b"sku,description,qty,unit\nHS-TIP,Suction tips,3,\n"
        invoice, lines = parse_invoice(self.org, csv, "text/csv")
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)
        with mock.patch("assistant.gemini.generate", return_value="not json"):
            invoice, lines = parse_invoice(self.org, b"x", "image/png")
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)
        self.assertEqual(lines, [])

    def test_unknown_supplier_is_kept_by_name_only(self):
        other = Organisation.objects.create(name="Other Dental")
        Supplier.objects.create(organisation=other, name="Dental Supplies Ltd")
        csv = b"vendor,sku,qty,unit\nDental Supplies Ltd,DS-1,1,5\n"
        invoice, _ = parse_invoice(self.org, csv, "text/csv")
        self.assertEqual(invoice.supplier_name, "Dental Supplies Ltd")
        self.assertIsNone(invoice.supplier)

    def test_other_files_are_refused(self):
        with self.assertRaises(ValueError):
            parse_invoice(self.org, b"x", "text/plain")

    def test_save_invoice_persists_the_invoice_and_its_lines(self):
        invoice, lines = parse_invoice(self.org, CSV, "text/csv")
        saved = save_invoice(invoice, lines)
        self.assertIsNotNone(saved.pk)
        self.assertEqual(list(Invoice.objects.get(pk=saved.pk).lines.order_by("pk")), lines)


NUMBERED = b"""vendor,invoice_number,sku,description,qty,unit,line_total
Henry Schein,INV-881,HS-GLV-M,"Nitrile gloves, size M",10,8.50,85.00
"""


class RepeatsAndConflictsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein")
        cls.gloves = Item.objects.create(organisation=cls.org, name="Nitrile gloves, size M", unit="box",
                                         supplier=cls.supplier)

    def upload(self, data, mime_type="text/csv", name="invoice.csv", force_reason=""):
        invoice, lines = parse_invoice(self.org, data, mime_type, name)
        match_lines(invoice, lines)
        return ingest(invoice, lines, self.admin, force_reason)

    def test_the_same_file_twice_is_ignored_logged_and_changes_nothing(self):
        OrderLine.objects.create(organisation=self.org, item=self.gloves, qty=10, ordered_by=self.admin)
        with mock.patch("assistant.gemini.generate", return_value=AI_REPLY):
            first = self.upload(b"%PDF scan", "application/pdf", "schein.pdf")
        counts = (StockEvent.objects.count(), ItemAlias.objects.count())
        self.assertEqual(counts[0], 1)  # the first one received the gloves
        with (mock.patch("assistant.gemini.generate") as generate,
              self.assertLogs("stock.invoices", "INFO") as logs):
            second = self.upload(b"%PDF scan", "application/pdf", "schein (1).pdf")
        generate.assert_not_called()  # the earlier reading is reused
        self.assertEqual((first.status, second.status), (Invoice.Status.PARTIAL, Invoice.Status.IGNORED))
        self.assertEqual((StockEvent.objects.count(), ItemAlias.objects.count()), counts)
        checksum = hashlib.sha256(b"%PDF scan").hexdigest()
        self.assertEqual((second.checksum, second.source_name), (checksum, "schein (1).pdf"))
        self.assertIn("'schein (1).pdf'", logs.output[0])
        self.assertIn(checksum, logs.output[0])

    def test_import_anyway_records_who_forced_it_and_why(self):
        self.upload(NUMBERED)
        forced = self.upload(NUMBERED, force_reason="The same order arrived twice")
        self.assertEqual(forced.status, Invoice.Status.PARSED)
        self.assertEqual((forced.forced_by, forced.force_reason), (self.admin, "The same order arrived twice"))

    def test_a_different_file_with_a_seen_supplier_and_number_is_ignored(self):
        self.upload(NUMBERED)
        rescan = NUMBERED.replace(b"INV-881", b"inv-881").replace(b"\n", b"\r\n")
        self.assertEqual(self.upload(rescan, name="rescan.csv").status, Invoice.Status.IGNORED)
        other_number = NUMBERED.replace(b"INV-881", b"INV-882")
        self.assertEqual(self.upload(other_number).status, Invoice.Status.PARSED)

    def test_an_unknown_supplier_goes_to_review(self):
        invoice = self.upload(NUMBERED.replace(b"Henry Schein", b"Nobody Ltd"))
        self.assertIsNone(invoice.supplier)
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)

    def test_an_unknown_product_goes_to_review_without_blocking_the_invoice(self):
        invoice = self.upload(NUMBERED + b"Henry Schein,INV-881,HS-DAM,Rubber dam clamps,1,40.00,40.00\n")
        self.assertEqual(invoice.status, Invoice.Status.PARSED)
        self.assertEqual([line.item for line in invoice.lines.order_by("pk")], [self.gloves, None])

    def test_a_line_total_that_disagrees_blocks_the_whole_invoice(self):
        invoice = self.upload(NUMBERED + b"Henry Schein,INV-881,HS-BIB,Patient bibs,2,24.95,49.92\n")
        self.assertFalse(invoice.totals_ok)
        self.assertEqual(invoice.status, Invoice.Status.CONFLICT)

    def test_more_than_is_outstanding_is_an_over_delivery(self):
        order = OrderLine.objects.create(organisation=self.org, item=self.gloves, qty=10, ordered_by=self.admin)
        self.upload(NUMBERED.replace(b",10,8.50,85.00", b",12,8.50,102.00"))
        order.refresh_from_db()
        self.assertEqual((order.received_qty, order.over_delivered, order.is_open), (12, True, False))
        self.assertEqual(StockEvent.objects.get(kind="received").qty, 12)
        self.assertEqual(OrderLine.objects.count(), 1)  # nothing left on back-order

    def test_the_database_stops_two_uploads_at_once_both_getting_in(self):
        first = self.upload(NUMBERED)
        for fields in ({"checksum": first.checksum}, {"supplier": self.supplier, "invoice_number": "inv-881"}):
            with self.subTest(fields), self.assertRaises(IntegrityError), transaction.atomic():
                Invoice.objects.create(organisation=self.org, **fields)
        # The second upload checked for a repeat before the first had saved.
        with mock.patch("stock.invoices.find_original", side_effect=[None, first]):
            second = self.upload(NUMBERED.replace(b"\n", b"\r\n"))
        self.assertEqual(second.status, Invoice.Status.IGNORED)


def invoice_csv(number, vendor="Henry Schein", qty=2):
    name = f"INV-{number}.csv"
    return name, f"vendor,invoice_number,description,qty,unit\n{vendor},INV-{number},Glove,{qty},8.50\n".encode(), "text/csv"


class BatchTests(TestCase):
    def practice(self, name="Test Dental"):
        org = Organisation.objects.create(name=name)
        admin = User.objects.create_user(f"sandy@{org.slug}.test", "pw", organisation=org, role=User.Role.ADMIN)
        supplier = Supplier.objects.create(organisation=org, name="Henry Schein")
        gloves = Item.objects.create(organisation=org, name="Gloves", unit="box", supplier=supplier)
        StockEvent.objects.create(organisation=org, item=gloves, user=admin, kind="count", qty=5)
        OrderLine.objects.create(organisation=org, item=gloves, qty=12, ordered_by=admin)
        return org, admin

    def batch(self, org, admin, files):
        batch = InvoiceBatch.objects.create(organisation=org, created_by=admin)
        InvoiceBatchFile.objects.bulk_create([
            InvoiceBatchFile(organisation=org, batch=batch, name=name, data=data, content_type=content_type)
            for name, data, content_type in files
        ])
        return batch

    def states(self, batch):
        return dict(Counter(batch.files.values_list("state", flat=True)))

    def stock(self, org):
        return {item.name: on_hand(item.events.all()) for item in Item.objects.for_org(org)}

    def test_twenty_files_keep_their_own_state_and_a_rerun_reads_only_whats_left(self):
        org, admin = self.practice()
        files = [invoice_csv(n) for n in range(17)]
        files += [invoice_csv(0)]  # the first one again
        files += [invoice_csv(99, vendor="Nobody Ltd")]
        files += [("smudged.jpg", b"jpeg bytes", "image/jpeg")]
        batch = self.batch(org, admin, files)

        with (mock.patch("assistant.gemini.generate", side_effect=gemini.GeminiError("Gemini couldn't read it")),
              self.assertLogs("stock.invoices", "ERROR")):
            run_batch(batch)
        self.assertEqual(self.states(batch), {"ingested": 17, "ignored": 1, "parsed": 1, "failed": 1})
        failed = batch.files.get(state="failed")
        self.assertEqual((failed.name, failed.error), ("smudged.jpg", "Gemini couldn't read it"))
        self.assertEqual(batch.files.get(data__isnull=False), failed)  # read files are deleted; this one's kept to retry
        self.assertEqual(batch.files.get(state="parsed").invoice.status, Invoice.Status.CONFLICT)

        reply = {"vendor": "Henry Schein", "invoice_number": "INV-500",
                 "lines": [{"description": "Glove", "qty": 1, "unit": 8.5, "line_total": 8.5}]}
        with (mock.patch("stock.invoices.parse_invoice", wraps=parse_invoice) as parse,
              mock.patch("assistant.gemini.generate", return_value=reply)):
            call_command("import_invoices", batch.pk)
        self.assertEqual([call.args[3] for call in parse.call_args_list], ["smudged.jpg"])
        self.assertEqual(self.states(batch), {"ingested": 18, "ignored": 1, "parsed": 1})
        self.assertEqual(Invoice.objects.filter(organisation=org).exclude(status="ignored").count(), 19)

    def test_a_run_killed_halfway_ends_with_the_same_stock_as_one_that_wasnt(self):
        files = [invoice_csv(n, qty=n + 1) for n in range(6)]
        steady, killed = self.practice("Steady Dental"), self.practice("Killed Dental")
        before = self.stock(killed[0])
        self.assertEqual(self.stock(steady[0]), before)
        run_batch(self.batch(*steady, files))
        self.assertEqual(self.stock(steady[0]), {"Gloves": 5 + 15})  # the 6th invoice had nothing left on order

        batch = self.batch(*killed, files)
        real_ingest, calls = invoices.ingest, []

        def ingest_then_killed(*args, **kwargs):
            invoice = real_ingest(*args, **kwargs)
            calls.append(invoice)
            if len(calls) == 3:
                raise KeyboardInterrupt  # after the invoice is saved, before its file is marked
            return invoice

        with mock.patch("stock.invoices.ingest", side_effect=ingest_then_killed), self.assertRaises(KeyboardInterrupt):
            run_batch(batch)
        self.assertEqual(self.states(batch), {"ingested": 2, "pending": 4})
        run_batch(batch)

        self.assertEqual(self.states(batch), {"ingested": 6})
        self.assertEqual(self.stock(killed[0]), self.stock(steady[0]))
        for model in (Invoice, ItemAlias):
            self.assertEqual(model.objects.filter(organisation=killed[0]).count(),
                             model.objects.filter(organisation=steady[0]).count())


class ReconcileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.schein = Supplier.objects.create(organisation=cls.org, name="Henry Schein")
        cls.dentsply = Supplier.objects.create(organisation=cls.org, name="Dentsply")
        cls.gloves = cls.item("Nitrile gloves, size M", supplier_sku="HS-GLV-M")
        cls.bibs = cls.item("Patient bibs")
        cls.tips = cls.item("Suction tips, disposable")
        cls.dentsply_tips = cls.item("Suction tips, disposable", supplier=cls.dentsply)

    @classmethod
    def item(cls, name, supplier=None, **fields):
        return Item.objects.create(organisation=cls.org, name=name, unit="box", supplier=supplier or cls.schein, **fields)

    def order(self, item, qty, **fields):
        return OrderLine.objects.create(organisation=self.org, item=item, supplier=item.supplier, qty=qty,
                                        ordered_by=self.admin, **fields)

    def upload(self, data):
        invoice, lines = parse_invoice(self.org, data, "text/csv")
        match_lines(invoice, lines)
        return ingest(invoice, lines, self.admin)

    def test_lines_match_open_orders_by_order_number_then_product_code_then_name(self):
        orders = [
            self.order(self.gloves, 10, order_ref="PO-1"),
            self.order(self.gloves, 10, order_ref="PO-2"),
            self.order(self.bibs, 4),
            self.order(self.tips, 50),
            self.order(self.dentsply_tips, 50),
        ]
        invoice = self.upload(b"""vendor,po_number,invoice_number,sku,description,qty,unit
Henry Schein,PO-2,INV-1,HS-GLV-M,Medium nitrile exam gloves,10,8.50
,,,HS-BIB,Patient bib,4,1.00
,,,HS-TIP,Disposable suction tips,50,0.20
""")
        self.assertEqual(invoice.status, Invoice.Status.COMPLETE)
        for order in orders:
            order.refresh_from_db()
        # PO-2's gloves by code, the bibs by name, and the tips by name among what's on order from
        # Henry Schein (two items share that name, so on its own it isn't a sure match).
        self.assertEqual([order.received_qty for order in orders], [None, 10, 4, 50, None])
        self.assertEqual((orders[1].received_by, orders[1].received_at is not None), (self.admin, True))
        self.assertEqual(sorted(StockEvent.objects.filter(kind="received").values_list("item__name", "qty")),
                         [("Nitrile gloves, size M", 10), ("Patient bibs", 4), ("Suction tips, disposable", 50)])
        self.assertEqual(list(invoice.lines.order_by("pk").values_list("order_line", flat=True)),
                         [orders[1].pk, orders[2].pk, orders[3].pk])
        self.bibs.refresh_from_db()
        self.gloves.refresh_from_db()
        self.assertEqual((self.bibs.supplier_sku, self.gloves.supplier_sku), ("HS-BIB", "HS-GLV-M"))

    def test_a_second_invoice_against_the_same_order_receives_only_the_remainder(self):
        order = self.order(self.gloves, 10, order_ref="PO-9")
        first = self.upload(b"""vendor,po_number,invoice_number,sku,description,qty,unit
Henry Schein,PO-9,INV-1,HS-GLV-M,Medium nitrile exam gloves,6,8.50
""")
        order.refresh_from_db()
        # The invoice's own line was fully matched, even though the order itself is only part filled.
        self.assertEqual((first.status, order.received_qty, order.is_open), (Invoice.Status.COMPLETE, 6, False))

        remainder = OrderLine.objects.get(item=self.gloves, received_at__isnull=True)
        self.assertEqual((remainder.qty, remainder.order_ref, remainder.split_from), (4, "PO-9", order))

        second = self.upload(b"""vendor,po_number,invoice_number,sku,description,qty,unit
Henry Schein,PO-9,INV-2,HS-GLV-M,Medium nitrile exam gloves,4,8.50
""")
        remainder.refresh_from_db()
        self.assertEqual((second.status, remainder.received_qty, remainder.is_open),
                         (Invoice.Status.COMPLETE, 4, False))
        self.assertFalse(OrderLine.objects.filter(item=self.gloves, received_at__isnull=True).exists())

    def test_a_line_with_nothing_on_order_is_left_unmatched(self):
        self.order(self.bibs, 4)
        invoice = self.upload(b"vendor,description,qty,unit\nHenry Schein,Patient bibs,4,1.00\nHenry Schein,Gloves,1,8.50\n")
        self.assertEqual(invoice.status, Invoice.Status.PARTIAL)
        self.assertEqual(StockEvent.objects.filter(kind="received").count(), 1)

    def test_a_line_with_nothing_on_order_can_be_added_straight_to_stock_and_undone(self):
        """A receipt, or anything ordered outside StockRoom: the line goes onto
        the shelf without an order, takes the invoice's price, and Undo puts both back."""
        self.gloves.price = Decimal("8.00")
        self.gloves.save(update_fields=["price"])
        invoice = self.upload(b"vendor,description,qty,unit\nHenry Schein,Nitrile gloves size M,3,9.25\n")
        line = invoice.lines.get()
        self.assertEqual((invoice.status, line.item, line.is_received), (Invoice.Status.PARSED, self.gloves, False))

        self.client.force_login(self.admin)
        self.assertRedirects(self.client.post(f"/invoices/line/{line.pk}/receive/", {"to_stock": "1"}),
                             f"/invoices/{invoice.pk}/")
        line.refresh_from_db()
        invoice.refresh_from_db()
        self.gloves.refresh_from_db()
        self.assertEqual((invoice.status, line.order_line_id, self.gloves.price),
                         (Invoice.Status.COMPLETE, None, Decimal("9.25")))
        self.assertEqual(StockEvent.objects.filter(kind="received").values_list("item", "qty").get(),
                         (self.gloves.pk, 3))

        self.client.post(f"/invoices/{invoice.pk}/undo/")
        line.refresh_from_db()
        invoice.refresh_from_db()
        self.gloves.refresh_from_db()
        self.assertEqual((invoice.status, line.stock_event_id, self.gloves.price),
                         (Invoice.Status.PARSED, None, Decimal("8.00")))
        self.assertFalse(StockEvent.objects.filter(kind="received").exists())

    def test_a_line_already_on_the_shelf_is_not_added_twice(self):
        invoice = self.upload(b"vendor,description,qty,unit\nHenry Schein,Nitrile gloves size M,3,9.25\n")
        line = invoice.lines.get()
        self.client.force_login(self.admin)
        for _ in range(2):
            self.client.post(f"/invoices/line/{line.pk}/receive/", {"to_stock": "1"})
        self.assertEqual(StockEvent.objects.filter(kind="received").count(), 1)


class FindingInvoicesTests(TestCase):
    """UX-03. Nothing in the app linked to an invoice once you'd left the batch
    page, so an invoice that needed checking - one that received nothing, and so
    left the practice's counts wrong - became unreachable and stayed that way."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein")

    def setUp(self):
        self.client.force_login(self.admin)

    def conflict(self, org=None, supplier_name="Dentsply", number="D-1"):
        return Invoice.objects.create(organisation=org or self.org, supplier_name=supplier_name,
                                      invoice_number=number, issued_on=date(2026, 9, 14),
                                      status=Invoice.Status.CONFLICT)

    def test_invoices_needing_checking_are_listed_with_a_way_into_them(self):
        invoice = self.conflict()
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get("/invoices/")

        self.assertContains(response, "Needs checking")
        self.assertContains(response, f'href="/invoices/{invoice.pk}/"')
        self.assertContains(response, 'no supplier called "Dentsply"')

    def test_a_finished_invoice_is_still_reachable(self):
        done = Invoice.objects.create(organisation=self.org, supplier=self.supplier, supplier_name="Henry Schein",
                                      invoice_number="HS-7", issued_on=date(2026, 9, 14),
                                      status=Invoice.Status.COMPLETE, totals_ok=True)
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get("/invoices/")

        self.assertContains(response, "Recent invoices and receipts")
        self.assertContains(response, f'href="/invoices/{done.pk}/"')

    def test_deliveries_says_how_many_need_checking(self):
        self.conflict(number="D-1")
        self.conflict(number="D-2")
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get("/deliveries/")

        self.assertContains(response, "2 invoices need checking")

    def test_deliveries_reads_normally_with_nothing_to_check(self):
        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get("/deliveries/")

        self.assertContains(response, "Upload an invoice or receipt")
        self.assertNotContains(response, "need checking")

    def test_another_practices_invoices_are_never_listed(self):
        other = Organisation.objects.create(name="Other Dental")
        theirs = self.conflict(org=other, supplier_name="Someone Else", number="X-9")
        mine = self.conflict()

        with self.settings(AI_API_KEY="test-key"):
            response = self.client.get("/invoices/")

        self.assertContains(response, f'href="/invoices/{mine.pk}/"')
        self.assertNotContains(response, f'href="/invoices/{theirs.pk}/"')
        self.assertNotContains(response, "Someone Else")
