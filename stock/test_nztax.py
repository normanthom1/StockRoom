"""The New Zealand tax rules, checked against the legislation and against
IRD's own published example numbers."""

from datetime import date
from decimal import Decimal

from django.test import SimpleTestCase

from . import nztax


class IncomeYearTests(SimpleTestCase):
    """Income Tax Act 2007 s YE 1: the standard balance date is 31 March, so
    the 2026 income year runs 1 April 2025 to 31 March 2026."""

    def test_31_march_is_the_last_day_of_the_year_it_names(self):
        self.assertEqual(nztax.income_year(date(2026, 3, 31)), 2026)

    def test_1_april_starts_the_next_one(self):
        self.assertEqual(nztax.income_year(date(2026, 4, 1)), 2027)
        self.assertEqual(nztax.income_year(date(2025, 4, 1)), 2026)

    def test_bounds_are_half_open_so_31_march_is_counted_once(self):
        start, end = nztax.income_year_bounds(2026)
        self.assertEqual((start, end), (date(2025, 4, 1), date(2026, 4, 1)))
        # The last day is inside, and the next year's first day is outside.
        self.assertTrue(start <= date(2026, 3, 31) < end)
        self.assertFalse(start <= date(2026, 4, 1) < end)

    def test_labelled_the_way_ird_writes_it(self):
        self.assertEqual(nztax.income_year_label(2026), "2025-26")
        self.assertEqual(nztax.income_year_end(2026), date(2026, 3, 31))

    def test_years_back_are_newest_first(self):
        self.assertEqual(nztax.income_years_back(date(2026, 9, 18), 3), [2027, 2026, 2025])


class RetentionTests(SimpleTestCase):
    """Tax Administration Act 1994 s 22(2) and GST Act 1985 s 75(3): seven
    years after the end of the income year the record relates to."""

    def test_seven_years_after_the_end_of_the_income_year(self):
        # Issued 12 August 2025, which is the 2026 income year, ending 31 March
        # 2026. Seven years on is 31 March 2033.
        self.assertEqual(nztax.retain_until(date(2025, 8, 12)), date(2033, 3, 31))

    def test_a_day_either_side_of_balance_date_lands_a_year_apart(self):
        self.assertEqual(nztax.retain_until(date(2026, 3, 31)), date(2033, 3, 31))
        self.assertEqual(nztax.retain_until(date(2026, 4, 1)), date(2034, 3, 31))


class GstNumberTests(SimpleTestCase):
    """A GST number is the registered person's IRD number, so it carries IRD's
    check digit. The AI reads these off photos, so a wrong one has to be caught."""

    VALID = ["49091850", "35901981", "49098576", "136410132"]

    def test_real_ird_numbers_pass(self):
        for number in self.VALID:
            with self.subTest(number):
                self.assertTrue(nztax.is_valid_gst_number(number))

    def test_punctuation_and_spaces_dont_matter(self):
        self.assertTrue(nztax.is_valid_gst_number("49-091-850"))
        self.assertTrue(nztax.is_valid_gst_number(" 49 091 850 "))

    def test_a_misread_digit_fails(self):
        for number in ["49091851", "49091860", "136410133"]:
            with self.subTest(number):
                self.assertFalse(nztax.is_valid_gst_number(number))

    def test_nonsense_fails_rather_than_raising(self):
        for number in ["", None, "abc", "1", "12345678", "9" * 12, "00000000"]:
            with self.subTest(number):
                self.assertFalse(nztax.is_valid_gst_number(number))

    def test_formatted_the_way_ird_prints_it(self):
        self.assertEqual(nztax.format_gst_number("49091850"), "49-091-850")
        self.assertEqual(nztax.format_gst_number("136410132"), "136-410-132")


class GstAmountTests(SimpleTestCase):
    """GST has been 15% since 1 October 2010, so it's 3/23 of a GST-inclusive
    amount."""

    def test_gst_in_a_gst_inclusive_amount(self):
        self.assertEqual(nztax.gst_from_inclusive(Decimal("115.00")), Decimal("15.00"))
        self.assertEqual(nztax.gst_from_inclusive(Decimal("230.00")), Decimal("30.00"))

    def test_rounded_to_the_cent(self):
        self.assertEqual(nztax.gst_from_inclusive(Decimal("100.00")), Decimal("13.04"))

    def test_nothing_in_nothing_out(self):
        self.assertIsNone(nztax.gst_from_inclusive(None))
