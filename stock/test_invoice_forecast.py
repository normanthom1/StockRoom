"""Invoice receipts feeding the runout forecast (#102)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import Organisation, User

from .humanize import back_order_text, runway_text
from .invoices import ingest, match_lines, parse_invoice
from .models import Item, OrderLine, StockEvent, Supplier
from .test_forecast import TODAY, run, weekly_counts
from .views import _forecast_for, _median_lead_days


class InvoiceForecastTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organisation.objects.create(name="Test Dental")
        cls.admin = User.objects.create_user("sandy@example.com", "pw", organisation=cls.org, role=User.Role.ADMIN)
        cls.supplier = Supplier.objects.create(organisation=cls.org, name="Henry Schein", lead_days=5)
        cls.gloves = Item.objects.create(organisation=cls.org, name="Nitrile gloves", unit="box", supplier=cls.supplier)
        now = timezone.now()
        for weeks_ago, qty in zip(range(8, -1, -1), range(100, 19, -10), strict=True):  # 10 boxes a week
            StockEvent.objects.create(organisation=cls.org, item=cls.gloves, user=cls.admin, kind="count", qty=qty,
                                      created_at=now - timedelta(weeks=weeks_ago, minutes=1))

    def order(self, qty, days_ago=0):
        return OrderLine.objects.create(organisation=self.org, item=self.gloves, qty=qty, ordered_by=self.admin,
                                        ordered_at=timezone.now() - timedelta(days=days_ago), order_ref="PO-7")

    def upload(self, qty, issued_on):
        invoice, lines = parse_invoice(self.org, (
            "invoice_date,vendor,po_number,invoice_number,description,qty,unit\n"
            f"{issued_on:%Y-%m-%d},Henry Schein,PO-7,INV-{qty},Nitrile gloves,{qty},8.50\n"
        ).encode(), "text/csv")
        match_lines(invoice, lines)
        return ingest(invoice, lines, self.admin)

    def forecast(self):
        self.gloves.refresh_from_db()
        return _forecast_for(self.gloves, timezone.localtime())

    def test_an_invoice_moves_runout_by_the_received_qty_over_weekly_usage(self):
        self.order(14)
        before = self.forecast()
        self.upload(14, timezone.localdate())
        after = self.forecast()
        self.assertAlmostEqual(after.weekly_usage, 10, delta=0.1)
        self.assertEqual(after.on_hand, before.on_hand + 14)
        self.assertAlmostEqual(after.days_left - before.days_left, 14 / (after.weekly_usage / 7), delta=0.1)

    def test_lead_time_runs_from_ordering_to_the_invoice_date_and_is_only_offered(self):
        ordered = self.order(20, days_ago=20)
        self.upload(20, timezone.localdate(ordered.ordered_at) + timedelta(days=8))
        self.assertEqual(_median_lead_days(self.supplier), 8)  # not the 20 days until it was uploaded

        self.client.force_login(self.admin)
        page = self.client.get("/suppliers/")
        self.assertContains(page, "Deliveries actually take ~8 days")
        self.assertContains(page, "Use this")
        self.supplier.refresh_from_db()
        self.assertEqual(self.supplier.lead_days, 5)

    def test_a_back_order_forecasts_on_whats_still_coming(self):
        self.order(5)
        self.upload(3, timezone.localdate())
        f = self.forecast()
        self.assertEqual(f.incoming, 2)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/"), back_order_text(f.run_out, 2, timezone.localdate()))

    def test_back_order_text(self):
        self.assertEqual(back_order_text(TODAY + timedelta(days=4), 2, TODAY), "Runs out Wed, 2 still coming")
        self.assertEqual(back_order_text(TODAY + timedelta(days=14), 2, TODAY), "Runs out 26 Sep, 2 still coming")
        self.assertEqual(back_order_text(None, 2, TODAY), "2 still coming")

    def test_runway_text(self):
        f = run(weekly_counts([10] * 8, start=110), lead=5)  # 30 boxes left, 10 a week: 21 days
        self.assertEqual(runway_text(f, TODAY), "Enough for about 3 weeks. Order again by Friday 25 Sep.")
