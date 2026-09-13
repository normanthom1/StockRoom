from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from .forecast import Status, forecast, round_order_qty, weekly_consumption
from .models import OrderLine, StockEvent

NOW = datetime(2026, 9, 12, 12, tzinfo=ZoneInfo("Pacific/Auckland"))
TODAY = NOW.date()


def ev(kind, qty=None, days_ago=0.0):
    return StockEvent(kind=kind, qty=qty, created_at=NOW - timedelta(days=days_ago))


def weekly_counts(usage, start=500):
    """A count at the start of each week, oldest week first, ending with a count now."""
    events, qty = [ev("count", start, days_ago=7 * len(usage))], start
    for weeks_ago, used in zip(range(len(usage) - 1, -1, -1), usage, strict=True):
        qty -= used
        events.append(ev("count", qty, days_ago=7 * weeks_ago))
    return events


def run(events, orders=(), lead=5, **kwargs):
    return forecast(events, list(orders), lead_days=lead, now=NOW, **kwargs)


class ForecastTests(SimpleTestCase):
    def test_no_history(self):
        f = run([])
        self.assertIsNone(f.on_hand)
        self.assertEqual(f.weekly_usage, 0)
        self.assertIsNone(f.days_left)
        self.assertIsNone(f.run_out)
        self.assertEqual(f.status, Status.OK)
        self.assertEqual(f.confidence, "low")
        self.assertEqual(f.order_qty, 1)

    def test_brand_new_item_is_low_confidence_with_a_range(self):
        f = run([ev("count", 20, days_ago=5), ev("used", 2, days_ago=4), ev("used", 3, days_ago=1)])
        self.assertEqual(f.on_hand, 15)
        self.assertAlmostEqual(f.weekly_usage, 7)  # 5 used over 5 days
        self.assertEqual(f.confidence, "low")
        self.assertAlmostEqual(f.days_left, 15)
        self.assertEqual(f.run_out_range, (TODAY + timedelta(days=10), TODAY + timedelta(days=30)))
        self.assertEqual(f.status, Status.ORDER_THIS_WEEK)

    def test_counts_are_the_truth_between_stocktakes(self):
        # 10 + 20 received - 16 = 14 used over a week; the lone tap is ignored.
        events = [ev("count", 10, days_ago=7), ev("received", 20, days_ago=5), ev("used", 1, days_ago=3)]
        f = run([*events, ev("count", 16)])
        self.assertAlmostEqual(f.weekly_usage, 14)
        self.assertEqual(f.on_hand, 16)

    def test_one_spike_week_is_left_out(self):
        f = run(weekly_counts([10, 10, 10, 40, 10, 10, 10, 10]))
        self.assertAlmostEqual(f.weekly_usage, 10)
        self.assertEqual(f.excluded_weeks, 1)
        self.assertEqual(f.confidence, "high")
        self.assertIsNone(f.run_out_range)

    def test_two_spike_weeks_in_a_row_are_kept(self):
        f = run(weekly_counts([10, 10, 10, 40, 40, 10, 10, 10]))
        self.assertAlmostEqual(f.weekly_usage, 17.5)
        self.assertEqual(f.excluded_weeks, 0)

    def test_taps_fill_in_weeks_with_no_recount(self):
        f = run([ev("count", 50, days_ago=14), ev("used", 3, days_ago=10), ev("used", 4, days_ago=2)])
        self.assertAlmostEqual(f.weekly_usage, 3.5)
        self.assertEqual(f.on_hand, 43)

    def test_taps_older_than_the_window_are_left_out(self):
        # Ten weeks of one tap a week and never a count: only the last 8 weeks count.
        taps = [ev("used", 1, days_ago=7 * weeks_ago + 3) for weeks_ago in range(9, -1, -1)]
        self.assertEqual(weekly_consumption(taps, NOW), [1.0] * 8)
        self.assertAlmostEqual(run(taps[1:]).weekly_usage, 1)

    def test_out_of_stock(self):
        f = run([ev("count", 20, days_ago=10), ev("out", days_ago=1)])
        self.assertEqual(f.on_hand, 0)
        self.assertEqual(f.days_left, 0)
        self.assertEqual(f.run_out, TODAY)
        self.assertEqual(f.status, Status.OUT)

    def test_open_order_stops_asking_for_a_reorder(self):
        events = weekly_counts([35, 35, 35, 35], start=200)  # 60 left, 12 days
        self.assertEqual(run(events).status, Status.ORDER_THIS_WEEK)
        f = run(events, orders=[OrderLine(qty=30)])
        self.assertEqual(f.status, Status.ON_ORDER)
        self.assertEqual(f.incoming, 30)

    def test_lead_time_pushes_an_item_into_order_now(self):
        events = weekly_counts([35, 35, 35, 35], start=200)
        f = run(events, lead=10)
        self.assertAlmostEqual(f.days_left, 12)
        self.assertEqual(f.status, Status.ORDER_NOW)
        self.assertEqual(f.run_out, TODAY + timedelta(days=12))
        self.assertEqual(f.order_by, TODAY - timedelta(days=1))  # 12 - 10 lead - 3 buffer

    def test_low_flag_caps_days_left_until_the_next_count(self):
        events = weekly_counts([10, 10, 10, 10], start=200)  # 160 left, 112 days
        f = run([*events, ev("low")])
        self.assertEqual(f.days_left, 4)
        self.assertEqual(f.status, Status.ORDER_NOW)
        self.assertEqual(run([ev("low")]).days_left, 4)  # even when never counted

        recount = run([*events, ev("low"), ev("count", 150)])
        self.assertAlmostEqual(recount.days_left, 105)

    def test_order_size(self):
        self.assertEqual(run(weekly_counts([35, 35, 35, 35], start=200)).order_qty, 70)
        self.assertEqual(run([], order_size=12).order_qty, 12)
        self.assertEqual([round_order_qty(n) for n in (0.2, 7.1, 12, 51)], [1, 8, 15, 60])
