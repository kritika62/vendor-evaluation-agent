import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vea.config import Settings
from vea.costs import parse_money, preview_costs
from vea.currency import CurrencyError

RATES = {"date": "test-date", "per_usd": {"USD": 1, "INR": 80, "JPY": 150, "EUR": 0.5}}


class FakeLLM:
    def __init__(self, answer):
        self.answer = answer

    def complete_json(self, system, user, *a, **k):
        return dict(self.answer)


class BrokenLLM:
    def complete_json(self, system, user, *a, **k):
        raise RuntimeError("boom")


def found(value, unit, quote):
    return {"found": True, "value": value, "unit": unit, "evidence_quote": quote, "pages": [1]}


class CostPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        rates = root / "rates.json"
        rates.write_text(json.dumps(RATES), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"VEA_RATES_FILE": str(rates)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.props = root / "proposals"
        self.props.mkdir()

    def _run(self, text, answer=None, currency="INR", mock_mode=False, llm=None):
        d = self.props / "Alpha"
        d.mkdir(exist_ok=True)
        (d / "p.txt").write_text(text, encoding="utf-8")
        out = preview_costs(str(self.props), currency, mock=mock_mode, llm=llm or FakeLLM(answer),
                            settings=Settings(mock=True))
        return out, out["vendors"][0]

    def test_usd_price_is_converted_into_the_chosen_currency(self):
        out, r = self._run("Pricing. The total price is 20,000 USD for the licence.",
                           found(20000, "USD", "total price is 20,000 USD"))
        self.assertEqual(out["currency"], "INR")
        self.assertEqual(out["rates_date"], "test-date")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["currency"], "USD")
        self.assertAlmostEqual(r["converted"], 1_600_000)
        self.assertIn("p.txt", r["source"])

    def test_euro_price_is_converted(self):
        _, r = self._run("Pricing. The total price is EUR 450,000 for three years.",
                         found(450000, "EUR", "total price is EUR 450,000"))
        self.assertAlmostEqual(r["converted"], 72_000_000)
        self.assertEqual(r["status"], "ok")

    def test_price_already_in_the_chosen_currency_needs_no_rates(self):
        missing = str(Path(self.tmp.name) / "nope.json")
        with mock.patch.dict(os.environ, {"VEA_RATES_FILE": missing}):
            _, r = self._run("Pricing. The total price is INR 100 for everything.",
                             found(100, "INR", "total price is INR 100"))
        self.assertEqual(r["converted"], 100)
        self.assertEqual(r["status"], "ok")

    def test_ambiguous_dollar_sign_is_flagged_not_guessed(self):
        _, r = self._run("Pricing. The total price is $ 5000 for everything.",
                         found(5000, "$", "total price is $ 5000"))
        self.assertIsNone(r["converted"])
        self.assertEqual(r["status"], "needs review")
        self.assertIn("ambiguous", r["note"])

    def test_no_price_found_is_flagged(self):
        _, r = self._run("Our platform covers all functional requirements.",
                         {"found": False, "value": 0, "unit": "", "evidence_quote": "", "pages": []})
        self.assertFalse(r["found"])
        self.assertEqual(r["status"], "needs review")
        self.assertIn("No total price", r["note"])

    def test_missing_exchange_rate_is_flagged(self):
        _, r = self._run("Pricing. The total price is GBP 900 for everything.",
                         found(900, "GBP", "total price is GBP 900"))
        self.assertIsNone(r["converted"])
        self.assertIn("rates.json", r["note"])

    def test_quote_not_in_the_text_is_flagged(self):
        _, r = self._run("Pricing. The total price is 20,000 USD for the licence.",
                         found(20000, "USD", "completely different words"))
        self.assertEqual(r["status"], "needs review")
        self.assertIn("verbatim", r["note"])
        self.assertIsNotNone(r["converted"])

    def test_llm_failure_is_reported_not_raised(self):
        _, r = self._run("Pricing. The total price is 20,000 USD.", llm=BrokenLLM())
        self.assertEqual(r["status"], "needs review")
        self.assertIn("could not read", r["note"])

    def test_ambiguous_chosen_currency_is_refused(self):
        with self.assertRaises(CurrencyError):
            self._run("Pricing. The total price is 20,000 USD.", found(1, "USD", "x"), currency="$")

    def test_parse_money(self):
        p = parse_money("The total licence and implementation cost is EUR 450,000 over three years.")
        self.assertEqual((p["value"], p["unit"]), (450000.0, "EUR"))
        p = parse_money("Annual support is 9 lakh rupees.")
        self.assertEqual((p["value"], p["unit"]), (900000.0, "rupees"))
        p = parse_money("priced at 600K dollars.")
        self.assertEqual((p["value"], p["unit"]), (600000.0, "dollars"))
        self.assertEqual(parse_money("USD 2.5 million total")["value"], 2500000.0)
        self.assertEqual(parse_money("INR 40 more than planned")["value"], 40.0)
        self.assertIsNone(parse_money("we deliver in 16 weeks with 99.9% uptime"))

    def test_mock_mode_reads_with_a_pattern_and_converts(self):
        _, r = self._run("Pricing. The total price is EUR 450,000 over three years.", mock_mode=True)
        self.assertTrue(r["found"])
        self.assertEqual(r["amount"], 450000.0)
        self.assertEqual(r["currency"], "EUR")
        self.assertAlmostEqual(r["converted"], 72_000_000)
        self.assertEqual(r["status"], "needs review")
        self.assertIn("offline", r["note"])


if __name__ == "__main__":
    unittest.main()
