"""UX-04. Django ships no en_NZ locale, so LANGUAGE_CODE = "en-nz" fell back to
"en" and every date rendered without an explicit format came out American:
"Sept. 18, 2026". On an invoice the date decides which tax year it lands in."""

from datetime import date

from django import forms
from django.core.exceptions import ValidationError
from django.template import Context, Template
from django.test import TestCase
from django.utils.formats import get_format

from accounts.models import Organisation, User
from stock.models import Invoice, Supplier


class NZDateFormatTests(TestCase):
    def test_a_date_with_no_format_filter_reads_day_first(self):
        rendered = Template("{{ d }}").render(Context({"d": date(2026, 9, 18)}))
        self.assertEqual(rendered, "18 September 2026")

    def test_the_locale_formats_are_ours_not_the_american_fallback(self):
        self.assertEqual(get_format("DATE_FORMAT"), "j F Y")
        self.assertEqual(get_format("SHORT_DATE_FORMAT"), "j M Y")

    def test_a_date_typed_the_nz_way_validates(self):
        field = forms.DateField()
        self.assertEqual(field.clean("18/09/2026"), date(2026, 9, 18))
        # What <input type="date"> posts still has to work.
        self.assertEqual(field.clean("2026-09-18"), date(2026, 9, 18))
        with self.assertRaises(ValidationError):
            field.clean("09/18/2026")  # month first is not a NZ date


class InvoiceDateTests(TestCase):
    """The screen that rendered issued_on with no filter, which is where the
    wrong order actually cost something."""

    def test_the_invoice_page_shows_the_issue_date_day_first(self):
        org = Organisation.objects.create(name="Test Dental")
        admin = User.objects.create_user("sandy@example.com", "pw", organisation=org, role=User.Role.ADMIN)
        supplier = Supplier.objects.create(organisation=org, name="Henry Schein")
        invoice = Invoice.objects.create(organisation=org, supplier=supplier, supplier_name="Henry Schein",
                                         issued_on=date(2026, 9, 18), status=Invoice.Status.COMPLETE, totals_ok=True)
        self.client.force_login(admin)

        response = self.client.get(f"/invoices/{invoice.pk}/")

        self.assertContains(response, "18 September 2026")
        self.assertNotContains(response, "Sept. 18, 2026")
