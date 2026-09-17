import hashlib
from datetime import date
from decimal import Decimal
from unittest import mock

from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import Organisation, User

from .invoices import ingest, match_lines, parse_invoice, save_invoice
from .models import Invoice, Item, ItemAlias, OrderLine, StockEvent, Supplier

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
        with mock.patch("assistant.gemini.generate", return_value=AI_REPLY):
            first = self.upload(b"%PDF scan", "application/pdf", "schein.pdf")
        counts = (StockEvent.objects.count(), ItemAlias.objects.count())
        with (mock.patch("assistant.gemini.generate") as generate,
              self.assertLogs("stock.invoices", "INFO") as logs):
            second = self.upload(b"%PDF scan", "application/pdf", "schein (1).pdf")
        generate.assert_not_called()  # the earlier reading is reused
        self.assertEqual((first.status, second.status), (Invoice.Status.PARSED, Invoice.Status.IGNORED))
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
        order.receive(12, self.admin)
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
