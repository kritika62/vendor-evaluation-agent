import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from vea.config import Settings
from vea.ingest import Chunk
from vea.retriever import Retriever
from vea.rubric import load_rubric
from vea.scorer import MONEY_NOTE, score_numeric

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "scorecard_template.xlsx"
RATES = {"date": "test-date", "per_usd": {"USD": 1, "INR": 80, "JPY": 150, "EUR": 0.5}}
TEXT = "The total price for the licence, implementation and three years of support is as quoted."


class SeqLLM:
    def __init__(self, answer):
        self.answer = answer
        self.systems = []

    def complete_json(self, system, user, *a, **k):
        self.systems.append(system)
        return dict(self.answer)


def found(value, unit):
    return {"found": True, "value": value, "unit": unit, "evidence_quote": "total price for the licence", "pages": [1]}


class CostScoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = Path(self.tmp.name) / "rates.json"
        p.write_text(json.dumps(RATES), encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"VEA_RATES_FILE": str(p)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _score(self, answer, unit="JPY", target=6_000_000):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        cost = next(c for c in criteria if c.id == "T7")      # a 'Lower is better' criterion, used as the cost stand-in
        cost.unit = unit
        retriever = Retriever([Chunk(vendor="V", page=1, text=TEXT, source="v.txt")], Settings(mock=True))
        llm = SeqLLM(answer)
        r = score_numeric("V", cost, retriever, llm, Settings(mock=False), {"value": float(target), "unit": unit}, min_s)
        return r, llm

    def test_usd_quote_is_converted_into_the_costing_currency(self):
        r, _ = self._score(found(20_000, "USD"))
        self.assertEqual(r.score, 5)
        self.assertIn("20,000 USD = 3,000,000.0 JPY", r.justification)
        self.assertIn("Exchange rates dated test-date", r.justification)

    def test_rupee_quote_is_converted_and_scored(self):
        r, _ = self._score(found(4_800_000, "INR"))
        self.assertEqual(r.score, 2.5)

    def test_quote_already_in_the_costing_currency_needs_no_rates(self):
        missing = str(Path(self.tmp.name) / "nope.json")
        with mock.patch.dict(os.environ, {"VEA_RATES_FILE": missing}):
            r, _ = self._score(found(3_000_000, "JPY"))
        self.assertEqual(r.score, 5)
        self.assertNotIn("unit problem", r.review_reason)
        self.assertNotIn("Exchange rates", r.justification)

    def test_ambiguous_dollar_sign_is_flagged_not_guessed(self):
        r, _ = self._score(found(20_000, "$"))
        self.assertEqual(r.score, 0)
        self.assertTrue(r.needs_review)
        self.assertIn("unit problem", r.review_reason)
        self.assertIn("ambiguous", r.review_reason)

    def test_missing_rate_is_flagged_with_advice(self):
        r, _ = self._score(found(100, "GBP"))
        self.assertEqual(r.score, 0)
        self.assertTrue(r.needs_review)
        self.assertIn("rates.json", r.review_reason)

    def test_money_rules_are_added_to_the_prompt_only_for_money(self):
        _, llm = self._score(found(20_000, "USD"))
        self.assertIn(MONEY_NOTE, llm.systems[0])
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t7 = next(c for c in criteria if c.id == "T7")
        retriever = Retriever([Chunk(vendor="V", page=1, text=TEXT, source="v.txt")], Settings(mock=True))
        llm2 = SeqLLM({"found": True, "value": 16, "unit": "weeks", "evidence_quote": "", "pages": [1]})
        score_numeric("V", t7, retriever, llm2, Settings(mock=False), {"value": 6.0, "unit": "months"}, min_s)
        self.assertNotIn(MONEY_NOTE, llm2.systems[0])


if __name__ == "__main__":
    unittest.main()
