import unittest
from datetime import date

from ptp import dates


class PregnancyDates(unittest.TestCase):
    def test_due_date_is_lmp_plus_280_days(self):
        self.assertEqual(dates.due_date(date(2026, 4, 24)), date(2027, 1, 29))

    def test_gestational_age(self):
        self.assertEqual(dates.gestational_age(date(2026, 4, 24), date(2026, 10, 9)), (24, 0))
        self.assertEqual(dates.gestational_age(date(2026, 4, 24), date(2026, 10, 12)), (24, 3))

    def test_future_lmp_rejected(self):
        with self.assertRaises(ValueError):
            dates.gestational_age(date(2026, 10, 10), date(2026, 10, 9))

    def test_trimester_edges(self):
        self.assertEqual([dates.trimester(w) for w in (0, 13, 14, 27, 28, 40)], [1, 1, 2, 2, 3, 3])

    def test_progress_is_clamped(self):
        self.assertEqual(dates.progress_fraction(date(2026, 1, 1), date(2027, 6, 1)), 1.0)
        self.assertAlmostEqual(dates.progress_fraction(date(2026, 4, 24), date(2026, 10, 9)), 168 / 280)


class RelativeDates(unittest.TestCase):
    thu = date(2026, 10, 8)  # a Thursday
    fri = date(2026, 10, 9)

    def test_the_sample_visit_sentence(self):
        r = dates.resolve("Balik kayo sa susunod na Huwebes, alas nuwebe ng umaga.", self.thu)
        self.assertEqual((r["date"], r["time"]), ("2026-10-15", "09:00"))

    def test_same_weekday_means_a_week_later(self):
        self.assertEqual(dates.resolve("next Thursday", self.thu)["date"], "2026-10-15")
        self.assertEqual(dates.resolve("next Thursday", self.fri)["date"], "2026-10-15")

    def test_tomorrow_and_day_after(self):
        self.assertEqual(dates.resolve("Bumalik kayo bukas", self.thu)["date"], "2026-10-09")
        self.assertEqual(dates.resolve("sa makalawa", self.thu)["date"], "2026-10-10")

    def test_month_and_day(self):
        self.assertEqual(dates.resolve("sa Oktubre 20", self.thu)["date"], "2026-10-20")
        self.assertEqual(dates.resolve("on Jan 5", self.thu)["date"], "2027-01-05")
        self.assertEqual(dates.resolve("20 ng Oktubre", self.thu)["date"], "2026-10-20")
        self.assertEqual(dates.resolve("Oct 20, 2027", self.thu)["date"], "2027-10-20")

    def test_in_n_days_or_weeks(self):
        self.assertEqual(dates.resolve("sa loob ng dalawang linggo", self.thu)["date"], "2026-10-22")
        self.assertEqual(dates.resolve("in 3 days", self.thu)["date"], "2026-10-11")
        self.assertEqual(dates.resolve("sa loob ng apat na araw", self.thu)["date"], "2026-10-12")

    def test_next_week_is_not_sunday(self):
        self.assertEqual(dates.resolve("sa susunod na linggo", self.thu)["date"], "2026-10-15")

    def test_capital_linggo_is_sunday(self):
        self.assertEqual(dates.resolve("sa Linggo po", self.thu)["date"], "2026-10-11")

    def test_no_date_phrase(self):
        r = dates.resolve("Magpa-blood test po kayo bago bumalik.", self.thu)
        self.assertIsNone(r["date"])
        self.assertIsNone(r["time"])

    def test_may_is_not_a_month(self):
        self.assertIsNone(dates.resolve("may 2 tablet na natira", self.thu)["date"])

    def test_earliest_phrase_wins(self):
        self.assertEqual(dates.resolve("bukas o sa Biyernes", self.thu)["date"], "2026-10-09")


class TimesOfDay(unittest.TestCase):
    def check(self, text, expected):
        self.assertEqual(dates.parse_time(text), expected, text)

    def test_formats(self):
        self.check("alas nuwebe ng umaga", "09:00")
        self.check("alas 9", "09:00")
        self.check("alas tres ng hapon", "15:00")
        self.check("alas tres", "15:00")
        self.check("ala una", "13:00")
        self.check("9am", "09:00")
        self.check("9:30 AM", "09:30")
        self.check("3 pm", "15:00")
        self.check("12 pm", "12:00")
        self.check("12 am", "00:00")
        self.check("10 ng umaga", "10:00")
        self.check("7 ng gabi", "19:00")
        self.check("14:45", "14:45")
        self.check("walang oras", None)


if __name__ == "__main__":
    unittest.main()
