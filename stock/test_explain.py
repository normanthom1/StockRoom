"""UX-08. StockRoom asks a manager to act on words it invented - "Matched
names", "Needs checking", "Confident", "Standard order size" - and defined none
of them. Someone who doesn't know what "Matched names" means never opens it, so
the review that keeps the stock list free of duplicates goes unused."""

from datetime import date

from django.test import TestCase
from django.utils.html import escape

from accounts.models import Organisation, User
from stock.humanize import (
    CONFIDENCE_HELP,
    INVOICE_STATUS_HELP,
    MATCHED_NAMES_HELP,
    ORDER_SIZE_HELP,
)
from stock.models import Invoice, Item, StockEvent, Supplier


class ExplainTests(TestCase):
    def setUp(self):
        self.org = Organisation.objects.create(name="Test Dental")
        self.admin = User.objects.create_user("sandy@example.com", "pw", organisation=self.org,
                                              role=User.Role.ADMIN)
        self.supplier = Supplier.objects.create(organisation=self.org, name="Henry Schein")
        self.item = Item.objects.create(organisation=self.org, name="Gloves", unit="box", supplier=self.supplier)
        StockEvent.objects.create(organisation=self.org, item=self.item, user=self.admin, kind="count", qty=5)
        self.client.force_login(self.admin)

    def test_the_item_page_explains_confidence_and_order_size(self):
        response = self.client.get(f"/item/{self.item.pk}/")

        # escaped: the copy has apostrophes, which Django writes as &#x27;
        self.assertContains(response, escape(ORDER_SIZE_HELP))
        self.assertContains(response, escape(CONFIDENCE_HELP[response.context["f"].confidence]))

    def test_matched_names_is_explained_where_it_is_offered_not_only_where_it_leads(self):
        """The link on Stock is where a manager decides whether to tap it."""
        for path in ("/items/", "/items/matched/"):
            with self.subTest(path):
                self.assertContains(self.client.get(path), escape(MATCHED_NAMES_HELP))

    def test_an_invoice_explains_whatever_status_it_is_in(self):
        for status in (Invoice.Status.PARSED, Invoice.Status.COMPLETE, Invoice.Status.PARTIAL,
                       Invoice.Status.CONFLICT, Invoice.Status.IGNORED):
            with self.subTest(status):
                invoice = Invoice.objects.create(organisation=self.org, supplier=self.supplier,
                                                 supplier_name="Henry Schein", issued_on=date(2026, 9, 18),
                                                 status=status, totals_ok=True)
                response = self.client.get(f"/invoices/{invoice.pk}/")
                self.assertContains(response, escape(INVOICE_STATUS_HELP[status]))

    def test_every_explanation_is_reachable_by_keyboard_and_labelled(self):
        """A <dialog> opened with showModal() handles focus and Escape itself;
        what it can't do is name the button that opens it."""
        response = self.client.get(f"/item/{self.item.pk}/")
        content = response.content.decode()

        self.assertIn('aria-label=\'What "Standard order size" means\'', content)
        # A real button, not a div with a click handler.
        self.assertIn('<button type="button" class="tap-target -my-2 grid shrink-0 place-items-center"', content)
        self.assertIn('data-open-dialog="explain-order-size"', content)
        self.assertIn('<dialog id="explain-order-size"', content)

    def test_every_explanation_is_one_sentence(self):
        """What UX-08 asked for: a line to read mid-task, not a paragraph."""
        import re

        for text in [*CONFIDENCE_HELP.values(), *INVOICE_STATUS_HELP.values(), ORDER_SIZE_HELP, MATCHED_NAMES_HELP]:
            self.assertIsNone(re.search(r"[.!?]\s+[A-Z]", text), text)

    def test_the_explanations_introduce_no_new_jargon(self):
        """An explanation that needs its own explanation is no use."""
        jargon = ("alias", "merge", "ingest", "parse", "normalise", "fuzzy", "embedding",
                  "SKU", "conflict", "idempotent", "checksum")
        texts = [*CONFIDENCE_HELP.values(), *INVOICE_STATUS_HELP.values(),
                 ORDER_SIZE_HELP, MATCHED_NAMES_HELP]
        for text in texts:
            for word in jargon:
                self.assertNotIn(word.lower(), text.lower(), f"{word!r} in {text!r}")


class TemplateCommentTests(TestCase):
    """Django's {# ... #} is single-line only. A multi-line one isn't a comment:
    every line of it is rendered into the page as text. This has shipped twice
    now - once onto the setup checklist, once into the explanation component
    itself, where the word "dialog" in the prose became a real closed <dialog>
    element and hid the button it wrapped. Cheaper to assert than to spot."""

    def test_no_template_has_a_multi_line_short_comment(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in root.rglob("*.html"):
            if any(part in (".venv", "staticfiles", ".django_tailwind_cli") for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"\{#", text):
                close = text.find("#}", match.start())
                if close == -1 or "\n" in text[match.start():close]:
                    line = text[:match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(root)}:{line}")
        self.assertEqual(offenders, [], "Use {% comment %} for anything over one line")
