from datetime import date

from django.test import SimpleTestCase

from .spending import period_bounds, previous_period_bounds


class PeriodBoundsTests(SimpleTestCase):
    def test_week_is_monday_to_monday(self):
        # 2026-09-12 is a Saturday.
        self.assertEqual(period_bounds("week", date(2026, 9, 12)), (date(2026, 9, 7), date(2026, 9, 14)))

    def test_month(self):
        self.assertEqual(period_bounds("month", date(2026, 9, 12)), (date(2026, 9, 1), date(2026, 10, 1)))

    def test_month_rolls_over_the_year(self):
        self.assertEqual(period_bounds("month", date(2026, 12, 25)), (date(2026, 12, 1), date(2027, 1, 1)))

    def test_year(self):
        self.assertEqual(period_bounds("year", date(2026, 9, 12)), (date(2026, 1, 1), date(2027, 1, 1)))


class PreviousPeriodBoundsTests(SimpleTestCase):
    def test_previous_three_weeks_oldest_first(self):
        bounds = previous_period_bounds("week", date(2026, 9, 7), 3)
        self.assertEqual(
            bounds,
            [
                (date(2026, 8, 17), date(2026, 8, 24)),
                (date(2026, 8, 24), date(2026, 8, 31)),
                (date(2026, 8, 31), date(2026, 9, 7)),
            ],
        )

    def test_previous_three_months_rolls_over_the_year(self):
        bounds = previous_period_bounds("month", date(2026, 1, 1), 3)
        self.assertEqual(
            bounds,
            [
                (date(2025, 10, 1), date(2025, 11, 1)),
                (date(2025, 11, 1), date(2025, 12, 1)),
                (date(2025, 12, 1), date(2026, 1, 1)),
            ],
        )

    def test_previous_three_years(self):
        bounds = previous_period_bounds("year", date(2026, 1, 1), 3)
        self.assertEqual(
            bounds,
            [
                (date(2023, 1, 1), date(2024, 1, 1)),
                (date(2024, 1, 1), date(2025, 1, 1)),
                (date(2025, 1, 1), date(2026, 1, 1)),
            ],
        )
