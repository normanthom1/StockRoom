import io
from contextlib import redirect_stdout
from datetime import timedelta

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from accounts.models import Organisation

from .backtest import backtest_item
from .models import OrderLine
from .test_forecast import NOW, ev, weekly_counts


def run(events, orders=(), lead=5):
    return backtest_item(events, list(orders), lead_days=lead, end=NOW)


class BacktestTests(SimpleTestCase):
    def test_steady_use_is_predicted_exactly_once_there_is_history(self):
        # Scored 2, 3, 4, 5 and 6 weeks back; 6 weeks back is the first count, with nothing to go on yet.
        r = run(weekly_counts([10] * 6))
        self.assertEqual([round(e, 6) for e in r.errors], [0, 0, 0, 0, -20])
        self.assertEqual(r.actuals, [20] * 5)

    def test_rising_use_shows_as_negative_bias(self):
        r = run(weekly_counts([10, 10, 10, 10, 20, 20, 20]))
        self.assertLess(r.bias, 0)
        self.assertGreater(r.mae, 0)

    def test_weeks_with_no_count_after_them_are_not_scored(self):
        # Counted weekly until 3 weeks ago, then only taps: only the cutoffs
        # 5 and 6 weeks back have a count at or after their 2 weeks.
        events = [ev("count", qty, days_ago=days) for qty, days in ((100, 42), (90, 35), (80, 28), (70, 21))]
        self.assertEqual(len(run([*events, ev("used", 5, days_ago=10)]).errors), 2)

    def test_a_stockout_the_forecast_saw_coming(self):
        # 10 left a week before running out at 10 a week: order now, lead time before it happened.
        r = run(weekly_counts([10] * 10, start=100))
        self.assertEqual((r.stockouts, r.missed), (1, 0))

    def test_a_stockout_the_forecast_missed_counts_once(self):
        events = [ev("count", 50, days_ago=60), ev("count", 49, days_ago=30), ev("out", days_ago=7), ev("count", 0)]
        r = run(events)
        self.assertEqual((r.stockouts, r.missed), (1, 1))

    def test_only_orders_open_at_the_time_count(self):
        events = [ev("count", 50, days_ago=60), ev("count", 49, days_ago=30), ev("out")]
        in_time = OrderLine(qty=10, ordered_at=NOW - timedelta(days=6))
        too_late = OrderLine(qty=10, ordered_at=NOW - timedelta(days=1))
        self.assertEqual(run(events, [in_time]).missed, 0)
        self.assertEqual(run(events, [too_late]).missed, 1)


class BacktestCommandTests(TestCase):
    def test_prints_an_accuracy_table_for_the_seeded_practice(self):
        call_command("seed_demo")
        Organisation.objects.create(name="Empty Dental")
        out = io.StringIO()
        with redirect_stdout(out):
            call_command("backtest_forecast")
        output = out.getvalue()
        self.assertIn("NZ Dentist: 2-week forecasts", output)
        self.assertRegex(output, r"Nitrile gloves, size M\s+13\s")
        self.assertRegex(output, r"Overall: \d+ forecasts, off by \d+% of actual use \(bias [+-]\d+%\)\. 1 stock-out, 0 missed\.")
        self.assertIn("Empty Dental: no stock history yet.", output)
