import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vea import currency as cur
from vea.numeric import convert, score_lower_is_better, search_hints, unit_pattern

RATES = {"date": "test", "per_usd": {"USD": 1, "INR": 80, "JPY": 150, "EUR": 0.5}}


class CurrencyTests(unittest.TestCase):
    def test_codes_symbols_and_words(self):
        self.assertEqual(cur.norm_currency("usd"), "USD")
        self.assertEqual(cur.norm_currency("\u20b9"), "INR")
        self.assertEqual(cur.norm_currency("rupees"), "INR")
        self.assertEqual(cur.norm_currency("\u20ac"), "EUR")
        self.assertEqual(cur.norm_currency("yen"), "JPY")
        self.assertIsNone(cur.norm_currency("weeks"))

    def test_ambiguous_symbols_are_refused(self):
        for u in ("$", "\u00a5", "dollars"):
            with self.assertRaises(cur.CurrencyError):
                cur.norm_currency(u)
            self.assertTrue(cur.is_currency(u))

    def test_conversion_goes_through_usd(self):
        self.assertAlmostEqual(cur.convert_currency(100, "USD", "INR", RATES), 8000)
        self.assertAlmostEqual(cur.convert_currency(8000, "INR", "JPY", RATES), 15000)
        self.assertEqual(cur.convert_currency(5, "INR", "rupees", RATES), 5.0)

    def test_missing_rate_gives_a_clear_error(self):
        with self.assertRaises(cur.CurrencyError) as ctx:
            cur.convert_currency(1, "USD", "GBP", RATES)
        self.assertIn("rates.json", str(ctx.exception))

    def test_numeric_convert_handles_currency_and_still_handles_time(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "r.json"
            p.write_text(json.dumps(RATES), encoding="utf-8")
            with mock.patch.dict(os.environ, {"VEA_RATES_FILE": str(p)}):
                self.assertAlmostEqual(convert(100, "USD", "INR"), 8000)
                with self.assertRaises(ValueError):
                    convert(5, "$", "INR")
        self.assertAlmostEqual(convert(52, "weeks", "months"), 12.0)
        with self.assertRaises(ValueError):
            convert(5, "USD", "weeks")

    def test_money_pattern_finds_amounts_but_not_other_numbers(self):
        pat = unit_pattern("INR")
        for text in ("EUR 450,000 over three years", "9 lakh rupees", "600K dollars", "\u20b9 40,00,000"):
            self.assertIsNotNone(pat.search(text), text)
        for text in ("16 weeks", "users 5000", "99.9% uptime"):
            self.assertIsNone(pat.search(text), text)
        self.assertIn("price", search_hints("INR"))
        self.assertIsNotNone(unit_pattern("months"))

    def test_cost_score_after_conversion(self):
        target = 6_000_000                                                    # budget in JPY
        usd = cur.convert_currency(20_000, "USD", "JPY", RATES)               # 3,000,000
        self.assertEqual(round(score_lower_is_better(usd, target), 1), 5.0)
        inr = cur.convert_currency(4_800_000, "INR", "JPY", RATES)            # 9,000,000
        self.assertEqual(round(score_lower_is_better(inr, target), 1), 2.5)


if __name__ == "__main__":
    unittest.main()
