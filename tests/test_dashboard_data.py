import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from vea.dashboard_data import compute, load_results
from vea.report import scorecard_rows, vendor_columns, write_workbook
from vea.rubric import load_rubric
from vea.scorer import CriterionScore

TEMPLATE = Path(__file__).resolve().parent.parent / "template" / "scorecard_template.xlsx"
SUMMARIES = {"overview": "o", "strengths": [], "gaps": [], "risks": []}

# whole-criterion scores (no sub-scores): same numbers the original 3-vendor tests used
DATA = {"A": [4, 4, 3, 5, 4, 4, 3, 4, 3], "B": [5, 3, 4, 3, 3, 3, 4, 3, 4], "C": [3, 5, 5, 2, 4, 5, 3, 3, 5]}


def plain_scores(criteria, vendor, vals):
    return [CriterionScore(vendor, c.id, c.name, s, "high", "just", "quote", [1], s < 3, "low" if s < 3 else "")
            for c, s in zip(criteria, vals)]


def make_workbook(path, data=DATA, with_subs=None):
    criteria, _, _ = load_rubric(str(TEMPLATE))
    scores = {v: plain_scores(criteria, v, vals) for v, vals in data.items()}
    if with_subs:                       # {vendor: {sub_id: score}}: build criterion scores from sub-scores
        for v, subvals in with_subs.items():
            new = []
            for c in criteria:
                if c.subs and any(s.id in subvals for s in c.subs):
                    subs = [CriterionScore(v, s.id, s.name, subvals[s.id], "high", "j", "q", [1], False, "",
                                           [f"{v}.pdf p.1"], [], c.id) for s in c.subs]
                    total = round(sum(s.weight * x.score for s, x in zip(c.subs, subs)), 2)
                    new.append(CriterionScore(v, c.id, c.name, total, "high", "j", "", [1], False, "", [], subs))
                else:
                    new.append(next(x for x in scores[v] if x.criterion_id == c.id))
            scores[v] = new
    summaries = {v: SUMMARIES for v in scores}
    write_workbook(str(TEMPLATE), path, criteria, scores, summaries, {}, {v: [f"{v}.pdf"] for v in scores})
    return criteria


def cell_of(path, criteria, vendor, item_id):
    wb = load_workbook(path)
    sc = wb["Scorecard"]
    col = next(s for n, s, _ in vendor_columns(sc) if n == vendor)
    row = next(r for kind, c, sub, r in scorecard_rows(criteria) if (sub.id if sub else c.id) == item_id)
    return f"{col}{row}"


class DashboardData(unittest.TestCase):
    def test_ranking_matches_agent_logic(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx"); make_workbook(p)
            out = compute(load_results(p))
        s = out["summary"].set_index("Vendor")
        self.assertAlmostEqual(s.loc["A", "Weighted score"], 3.90)
        self.assertEqual(s.loc["A", "Recommendation"], "Shortlist")
        self.assertEqual(s.loc["C", "Status"], "Fail")          # fails mandatory T4
        self.assertEqual(list(out["categories"].index)[:2], ["Functionality", "Architecture"])
        self.assertEqual(len(out["flagged"]), 1)                # only C's T4 (score 2) was flagged

    def test_whatif_weights_and_rules(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx"); make_workbook(p)
            res = load_results(p)
        out = compute(res, min_score=2, shortlist_n=1)
        s = out["summary"].set_index("Vendor")
        self.assertEqual(s.loc["C", "Status"], "Pass")
        self.assertEqual(int((out["summary"]["Recommendation"] == "Shortlist").sum()), 1)
        w = {c.id: 1 for c in res["criteria"]}
        eq = compute(res, weights=w)["summary"].set_index("Vendor")
        self.assertAlmostEqual(eq.loc["A", "Weighted score"], sum(DATA["A"]) / 9, places=2)

    def test_human_override_in_excel_is_picked_up(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx"); crit = make_workbook(p)
            wb = load_workbook(p); wb["Scorecard"][cell_of(p, crit, "C", "T4")] = 4; wb.save(p)   # C's T4: 2 -> 4
            out = compute(load_results(p))
        self.assertEqual(out["summary"].set_index("Vendor").loc["C", "Status"], "Pass")

    def test_more_than_three_vendors(self):
        data = {f"V{i}": [(i + j) % 4 + 2 for j in range(9)] for i in range(12)}   # 12 vendors
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx"); make_workbook(p, data)
            res = load_results(p)
            out = compute(res)
        self.assertEqual(len(res["scores"]), 12)
        self.assertEqual(len(out["summary"]), 12)
        self.assertEqual(list(out["criteria_scores"].columns), list(data))

    def test_sub_scores_roll_up_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx")
            crit = make_workbook(p, {"A": DATA["A"]}, with_subs={"A": {"T4.1": 5, "T4.2": 5, "T4.3": 1, "T4.4": 1}})
            res = load_results(p)
            self.assertEqual(res["scores"]["A"]["T4"], 3.0)            # equal weights: (5+5+1+1)/4
            self.assertEqual(res["sub_scores"]["A"]["T4.3"], 1)
            # override one sub-score in Excel -> criterion score follows, with no Excel recalculation needed
            wb = load_workbook(p); wb["Scorecard"][cell_of(p, crit, "A", "T4.3")] = 5; wb.save(p)
            self.assertEqual(load_results(p)["scores"]["A"]["T4"], 4.0)   # (5+5+5+1)/4
            # typing a plain number over the criterion's formula cell overrides the whole criterion
            wb = load_workbook(p); wb["Scorecard"][cell_of(p, crit, "A", "T4")] = 2; wb.save(p)
            res = load_results(p)
            self.assertEqual(res["scores"]["A"]["T4"], 2)
            self.assertEqual(compute(res)["summary"].set_index("Vendor").loc["A", "Status"], "Fail")

    def test_sub_scores_appear_in_log_and_files_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.xlsx")
            make_workbook(p, {"A": DATA["A"]}, with_subs={"A": {"T4.1": 5, "T4.2": 5, "T4.3": 1, "T4.4": 1}})
            res = load_results(p)
        log = res["log"]
        self.assertEqual(int((log["Level"] == "Sub-criterion").sum()), 4)
        self.assertEqual(list(res["files"]["File"]), ["A.pdf"])


if __name__ == "__main__":
    unittest.main()


class DecisionSummaryTests(unittest.TestCase):
    def test_selected_qualified_and_rejected_have_reasons(self):
        from vea.exports import _decision, export
        data = {"Acme": [5, 5, 4, 5, 5, 4, 4, 5, 4], "Beta": [4, 4, 3, 4, 4, 4, 3, 4, 3],
                "Gamma": [3, 3, 3, 3, 3, 3, 3, 3, 3], "Delta": [5, 4, 5, 2, 4, 5, 3, 1, 5]}
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "s.xlsx"
            make_workbook(str(path), data)
            res = load_results(str(path))
            out = compute(res)
        dec = {e["vendor"]: e for e in _decision(res, out)}
        self.assertEqual(dec["Acme"]["group"], "selected")
        self.assertEqual(dec["Gamma"]["group"], "qualified")
        self.assertEqual(dec["Delta"]["group"], "rejected")
        self.assertTrue(any("T4" in r and "below" in r for r in dec["Delta"]["reasons"]))
        for fmt in ("pdf", "docx", "md", "json"):
            self.assertGreater(len(export(res, out, fmt)[0]), 500)
