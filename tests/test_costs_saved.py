"""Part 3: the costs are saved with each evaluation. Offline: mock mode and fake AI only, no Groq tokens."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api import service
from api.service import ServiceError
from tests.test_api_service import ApiBase, wait_done
from vea.pipeline import EvaluationError, costs_path_for_out, load_costs, run_evaluation

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "scorecard_template.xlsx"
TEXTS = {
    "Alpha": "=== Page 1 ===\nAlpha covers the requirements. Price: INR 9,00,000 per year for a 3 years contract.\n",
    "Beta": "=== Page 1 ===\nBeta covers the requirements. Total price USD 50,000. Go-live in 9 months.\n",
}


def make_proposals(d):
    folder = Path(d) / "proposals"
    folder.mkdir()
    for v, t in TEXTS.items():
        (folder / f"{v}.txt").write_text(t, encoding="utf-8")
    return str(folder)


def run(d, **kw):
    return run_evaluation(make_proposals(d) if not (Path(d) / "proposals").exists() else str(Path(d) / "proposals"),
                          str(Path(d) / "out" / "scorecard.xlsx"), template=str(TEMPLATE), mock=True,
                          interactive=False, target_overrides={"T6": 99.9, "T7": 6}, **kw)


class PipelineSavesCosts(unittest.TestCase):
    def test_costs_are_saved_next_to_the_scorecard(self):
        with tempfile.TemporaryDirectory() as d:
            res = run(d, cost_currency="INR")
            self.assertEqual(res["costs_path"], str(Path(d) / "out" / "costs.json"))
            saved = load_costs(res["out"])
            self.assertEqual(saved, json.loads(json.dumps(res["costs"])))
            self.assertEqual(saved["currency"], "INR")
            self.assertEqual(saved["error"], "")
            self.assertEqual({r["vendor"] for r in saved["vendors"]}, {"Alpha", "Beta"})
            alpha = next(r for r in saved["vendors"] if r["vendor"] == "Alpha")
            self.assertEqual(alpha["basis"], "per_year")
            self.assertAlmostEqual(alpha["term_years"], 3.0)
            self.assertAlmostEqual(alpha["total"], 2_700_000.0)       # 9 lakh x 3 years, calculated in code

    def test_no_currency_means_nothing_saved(self):
        with tempfile.TemporaryDirectory() as d:
            res = run(d)
            self.assertIsNone(res["costs"])
            self.assertIsNone(res["costs_path"])
            self.assertFalse(costs_path_for_out(res["out"]).exists())
            self.assertIsNone(load_costs(res["out"]))

    def test_costs_never_change_the_scores(self):
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            without = run(d1)["rows"]
            with_costs = run(d2, cost_currency="USD")["rows"]
            self.assertEqual(without, with_costs)

    def test_a_cost_failure_does_not_fail_the_evaluation(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("vea.costs.preview_costs", side_effect=RuntimeError("boom")):
                res = run(d, cost_currency="INR")
            self.assertTrue(res["rows"])
            self.assertIn("boom", res["costs"]["error"])
            self.assertEqual(res["costs"]["vendors"], [])
            self.assertIn("boom", load_costs(res["out"])["error"])

    def test_unknown_currency_is_refused_before_any_work(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(EvaluationError):
                run(d, cost_currency="ZZZ")


class ServiceSavesCosts(ApiBase):
    def test_evaluation_started_with_a_cost_currency_has_costs(self):
        self.upload_all()
        service.save_vendor_files("Priced", [("Priced.txt", io.BytesIO(
            b"=== Page 1 ===\nPriced: total price EUR 120,000. Go-live in 6 months.\n"))])
        job = service.start_evaluation(mock=True, targets={"T6": 99.9, "T7": 6}, cost_currency="inr")
        self.assertEqual(job["options"]["cost_currency"], "INR")
        self.assertEqual(wait_done(job["id"])["status"], "done")
        costs = service.get_costs(job["id"])
        self.assertTrue(costs["saved"])
        self.assertEqual(costs["currency"], "INR")
        priced = next(r for r in costs["vendors"] if r["vendor"] == "Priced")
        self.assertEqual(priced["basis"], "total")
        self.assertEqual(priced["written_currency"], "EUR")

    def test_evaluation_without_a_cost_currency_reports_no_saved_costs(self):
        eval_id = self.run_mock()
        costs = service.get_costs(eval_id)
        self.assertFalse(costs["saved"])
        self.assertEqual(costs["vendors"], [])

    def test_bad_cost_currency_and_unknown_evaluation(self):
        self.upload_all()
        with self.assertRaises(ServiceError) as cm:
            service.start_evaluation(mock=True, cost_currency="ZZZ")
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(ServiceError) as cm:
            service.get_costs("20990101-000000-abcdef")
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
