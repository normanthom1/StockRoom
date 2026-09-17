"""Stock expenses by income year, and the documents kept behind them."""

import hashlib
from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import Organisation, User

from . import invoices as invoices_module
from . import taxreport
from .models import Invoice, InvoiceDocument, InvoiceLine, Supplier

GST_NUMBER = "49091850"


def invoice(org, supplier=None, issued_on=date(2025, 8, 12), excl="100.00", gst="15.00", incl="115.00",
            status=Invoice.Status.PARSED, gst_number=GST_NUMBER, number="INV-1"):
    return Invoice.objects.create(
        organisation=org, supplier=supplier, supplier_name=supplier.name if supplier else "Henry Schein",
        issued_on=issued_on, status=status, invoice_number=number, supplier_gst_number=gst_number,
        total_excl_gst=None if excl is None else Decimal(excl),
        gst_amount=None if gst is None else Decimal(gst),
        total_incl_gst=None if incl is None else Decimal(incl),
    )


class YearTotalTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")

    def test_adds_up_a_year_and_splits_out_the_gst(self):
        invoice(self.org, self.supplier, issued_on=date(2025, 8, 12))
        invoice(self.org, self.supplier, issued_on=date(2026, 3, 31), number="INV-2")

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual(total.invoices, 2)
        self.assertEqual(total.excl_gst, Decimal("200.00"))
        self.assertEqual(total.gst, Decimal("30.00"))
        self.assertEqual(total.incl_gst, Decimal("230.00"))
        self.assertTrue(total.is_complete)

    def test_the_year_runs_1_april_to_31_march(self):
        invoice(self.org, self.supplier, issued_on=date(2025, 3, 31), number="A")  # 2025 year
        invoice(self.org, self.supplier, issued_on=date(2025, 4, 1), number="B")  # 2026 year
        invoice(self.org, self.supplier, issued_on=date(2026, 4, 1), number="C")  # 2027 year

        self.assertEqual(taxreport.year_total(self.org, 2025).invoices, 1)
        self.assertEqual(taxreport.year_total(self.org, 2026).invoices, 1)
        self.assertEqual(taxreport.year_total(self.org, 2027).invoices, 1)

    def test_a_duplicate_is_excluded_from_the_total_and_counted_separately(self):
        """The whole point of the repeat rules: uploading the same invoice
        twice must never double what the practice claims."""
        invoice(self.org, self.supplier)
        invoice(self.org, self.supplier, status=Invoice.Status.IGNORED, number="INV-1")

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual(total.invoices, 1)
        self.assertEqual(total.incl_gst, Decimal("115.00"))
        self.assertEqual(total.duplicates_ignored, 1)

    def test_gst_is_worked_out_when_the_invoice_didnt_show_it(self):
        invoice(self.org, self.supplier, excl=None, gst=None, incl="115.00")

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual(total.gst, Decimal("15.00"))
        self.assertEqual(total.excl_gst, Decimal("100.00"))
        self.assertEqual(total.gst_estimated, 1)
        self.assertFalse(total.is_complete)

    def test_a_missing_third_number_is_filled_from_the_other_two(self):
        invoice(self.org, self.supplier, excl="100.00", gst="15.00", incl=None)
        self.assertEqual(taxreport.year_total(self.org, 2026).incl_gst, Decimal("115.00"))

    def test_falls_back_to_the_lines_when_there_is_no_total_and_says_so(self):
        inv = invoice(self.org, self.supplier, excl=None, gst=None, incl=None)
        InvoiceLine.objects.create(organisation=self.org, invoice=inv, description="Gloves",
                                   qty=2, unit_price=Decimal("25.00"), line_total=Decimal("50.00"))

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual(total.excl_gst, Decimal("50.00"))
        self.assertEqual(total.without_totals, 1)

    def test_an_invoice_with_no_amounts_at_all_is_left_out_and_flagged(self):
        invoice(self.org, self.supplier, excl=None, gst=None, incl=None)

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual((total.invoices, total.unusable), (0, 1))
        self.assertEqual(total.incl_gst, Decimal("0.00"))

    def test_counts_what_would_stop_a_gst_claim(self):
        invoice(self.org, self.supplier, gst_number="")

        total = taxreport.year_total(self.org, 2026)

        self.assertEqual(total.missing_gst_number, 1)
        self.assertEqual(total.no_document, 1)

    def test_grouped_by_supplier_biggest_first(self):
        other = Supplier.objects.create(organisation=self.org, name="Aluro Healthcare")
        invoice(self.org, self.supplier, excl="10.00", gst="1.50", incl="11.50", number="A")
        invoice(self.org, other, excl="100.00", gst="15.00", incl="115.00", number="B")

        by_supplier = taxreport.year_total(self.org, 2026).by_supplier

        self.assertEqual([s.name for s in by_supplier], ["Aluro Healthcare", "Henry Schein"])
        self.assertEqual(by_supplier[0].incl_gst, Decimal("115.00"))

    def test_never_counts_another_practices_invoices(self):
        other_org = Organisation.objects.create(name="Other Dental")
        other_supplier = Supplier.objects.create(organisation=other_org, name="Henry Schein")
        invoice(other_org, other_supplier)

        self.assertEqual(taxreport.year_total(self.org, 2026).invoices, 0)

    def test_undated_invoices_are_in_no_year_and_listed_to_be_fixed(self):
        invoice(self.org, self.supplier, issued_on=None)

        self.assertEqual(taxreport.year_total(self.org, 2026).invoices, 0)
        self.assertEqual(taxreport.undated(self.org).count(), 1)

    def test_years_offered_cover_every_invoice_and_always_the_current_one(self):
        invoice(self.org, self.supplier, issued_on=date(2023, 5, 1), number="A")

        years = taxreport.years_with_invoices(self.org, date(2026, 9, 18))

        self.assertEqual(years[0], 2027)  # the current year, newest first
        self.assertIn(2024, years)  # the year the old invoice falls in


class DocumentRetentionTests(TestCase):
    """The original file is the tax record; the parsed lines are only
    StockRoom's reading of it."""

    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.user = User.objects.create_user("m@test.test", "pw", organisation=self.org, role=User.Role.ADMIN)

    def test_keeps_the_file_with_a_seven_year_retention_date(self):
        document = invoices_module.keep_document(
            self.org, self.user, "aug.pdf", "application/pdf", b"%PDF bytes", issued_on=date(2025, 8, 12)
        )

        self.assertEqual(bytes(document.data), b"%PDF bytes")
        self.assertEqual(document.byte_size, 10)
        self.assertEqual(document.retain_until, date(2033, 3, 31))

    def test_the_same_file_is_kept_once_however_often_it_is_uploaded(self):
        first = invoices_module.keep_document(self.org, self.user, "a.pdf", "application/pdf", b"same bytes")
        again = invoices_module.keep_document(self.org, self.user, "copy.pdf", "application/pdf", b"same bytes")

        self.assertEqual(first.pk, again.pk)
        self.assertEqual(InvoiceDocument.objects.for_org(self.org).count(), 1)

    def test_linked_to_its_invoice_by_the_checksum_of_the_same_bytes(self):
        """A single upload is kept before the preview, so it's only matched to
        its invoice once the manager confirms."""
        data = b"%PDF invoice"
        invoices_module.keep_document(self.org, self.user, "a.pdf", "application/pdf", data)
        inv = invoice(self.org, issued_on=date(2025, 8, 12))
        inv.checksum = hashlib.sha256(data).hexdigest()
        inv.save()

        invoices_module.link_documents(inv)

        document = InvoiceDocument.objects.get()
        self.assertEqual(document.invoice, inv)
        # Kept at least as long as that invoice's own income year requires.
        self.assertGreaterEqual(document.retain_until, date(2033, 3, 31))

    def test_linking_never_shortens_a_retention_period(self):
        data = b"%PDF old"
        document = invoices_module.keep_document(self.org, self.user, "a.pdf", "application/pdf", data,
                                                 issued_on=date(2026, 4, 1))  # retained to 2034
        inv = invoice(self.org, issued_on=date(2020, 1, 1))  # would only need 2028
        inv.checksum = hashlib.sha256(data).hexdigest()
        inv.save()

        invoices_module.link_documents(inv)

        document.refresh_from_db()
        self.assertEqual(document.retain_until, date(2034, 3, 31))


class TaxYearPageTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.user = User.objects.create_user("m@test.test", "pw", organisation=self.org, role=User.Role.ADMIN)
        self.supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
        self.client.force_login(self.user)

    def test_shows_the_year_total(self):
        invoice(self.org, self.supplier, issued_on=date(2025, 8, 12))

        response = self.client.get(reverse("stock:tax_year"), {"year": 2026})

        self.assertEqual(response.context["total"].incl_gst, Decimal("115.00"))
        self.assertContains(response, "115.00")

    def test_says_it_is_not_a_full_set_of_expenses(self):
        response = self.client.get(reverse("stock:tax_year"))
        self.assertContains(response, "isn&#x27;t a full set of business expenses")

    def test_a_year_with_no_invoices_still_renders(self):
        response = self.client.get(reverse("stock:tax_year"))
        self.assertEqual(response.status_code, 200)

    def test_the_csv_carries_the_checksum_so_a_figure_can_be_traced(self):
        inv = invoice(self.org, self.supplier, issued_on=date(2025, 8, 12))
        inv.checksum = "a" * 64
        inv.save()

        response = self.client.get(reverse("stock:export_tax_year"), {"year": 2026})
        body = response.content.decode("utf-8-sig")

        self.assertIn("a" * 64, body)
        self.assertIn("49-091-850", body)
        self.assertIn("counted", body)

    def test_the_csv_marks_a_duplicate_as_excluded_rather_than_dropping_it(self):
        invoice(self.org, self.supplier, issued_on=date(2025, 8, 12), status=Invoice.Status.IGNORED)

        body = self.client.get(reverse("stock:export_tax_year"), {"year": 2026}).content.decode("utf-8-sig")

        self.assertIn("excluded: already counted", body)

    def test_the_original_file_can_be_opened(self):
        inv = invoice(self.org, self.supplier)
        document = InvoiceDocument.objects.create(
            organisation=self.org, invoice=inv, filename="aug.pdf", content_type="application/pdf",
            data=b"%PDF bytes", byte_size=10, checksum="b" * 64, uploaded_by=self.user,
            retain_until=date(2033, 3, 31),
        )

        response = self.client.get(reverse("stock:invoice_document", args=[document.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"%PDF bytes")
        self.assertIn("aug.pdf", response["Content-Disposition"])
