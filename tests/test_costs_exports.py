"""Part 4: the saved costs appear in every download and in the Excel workbook. Offline: mock mode only."""
import io
import json
import tempfile
import unittest

import openpyxl

from api import service
from tests.test_api_service import ApiBase, wait_done
from tests.test_costs_saved import run
from vea.dashboard_data import compute, load_results
from vea.exports import FORMATS, export
from vea.pipeline import load_costs

HEADING = "Costs (information only)"


def build(d, cost_currency="INR"):
    r = run(d, cost_currency=cost_currency)
    res = load_results(r["out"])
    costs = load_costs(r["out"])
    if costs:
        res = {**res, "costs": costs}
    return r, res


class ExportsWithCosts(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.r, self.res = build(self.d.name)
        self.out = compute(self.res)

    def tearDown(self):
        self.d.cleanup()

    def get(self, fmt):
        return export(self.res, self.out, fmt)[0]

    def test_markdown_has_a_costs_table(self):
        md = self.get("md").decode("utf-8")
        self.assertIn(HEADING, md)
        self.assertIn("Per year", md)
        self.assertIn("2,700,000 INR", md)                  # Alpha: 9 lakh x 3 years, calculated in code
        self.assertIn("Information only", md)

    def test_json_carries_the_costs(self):
        data = json.loads(self.get("json"))
        self.assertEqual(data["costs"]["currency"], "INR")
        alpha = next(v for v in data["costs"]["vendors"] if v["vendor"] == "Alpha")
        self.assertEqual(alpha["basis"], "per_year")
        self.assertAlmostEqual(alpha["total"], 2_700_000.0)

    def test_csv_gets_cost_columns_on_each_ranking_row(self):
        import pandas as pd
        df = pd.read_csv(io.BytesIO(self.get("csv")))
        for col in ("Price as written", "Quoted as", "Contract years", "Total over contract", "Total in INR"):
            self.assertIn(col, df.columns)
        alpha = df[df["Vendor"] == "Alpha"].iloc[0]
        self.assertEqual(alpha["Quoted as"], "Per year")
        self.assertEqual(alpha["Total over contract"], "2,700,000 INR")

    def test_word_has_a_costs_heading_and_table(self):
        import docx
        doc = docx.Document(io.BytesIO(self.get("docx")))
        self.assertIn(HEADING, [p.text for p in doc.paragraphs])
        cells = [c.text for t in doc.tables for row in t.rows for c in row.cells]
        self.assertIn("2,700,000 INR", cells)

    def test_pdf_builds_with_costs(self):
        pdf = self.get("pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)

    def test_excel_workbook_has_a_costs_sheet(self):
        wb = openpyxl.load_workbook(self.r["out"])
        self.assertIn("Costs", wb.sheetnames)
        rows = {row[0]: row for row in wb["Costs"].iter_rows(min_row=2, values_only=True) if row[0] in ("Alpha", "Beta")}
        self.assertEqual(rows["Alpha"][4], "Per year")
        self.assertAlmostEqual(rows["Alpha"][6], 2_700_000.0)
        self.assertEqual(wb["Costs"]["A1"].value, "Vendor")
        self.assertIn("Total in INR", [c.value for c in wb["Costs"][1]])

    def test_scores_are_the_same_with_or_without_costs(self):
        plain = compute({k: v for k, v in self.res.items() if k != "costs"})
        self.assertEqual(plain["summary"].to_dict(), self.out["summary"].to_dict())


class ExportsWithoutCosts(unittest.TestCase):
    def test_nothing_changes_when_no_costs_were_saved(self):
        with tempfile.TemporaryDirectory() as d:
            r = run(d)
            res = load_results(r["out"])
            out = compute(res)
            self.assertNotIn("Costs", openpyxl.load_workbook(r["out"]).sheetnames)
            self.assertNotIn(HEADING, export(res, out, "md")[0].decode("utf-8"))
            self.assertNotIn("costs", json.loads(export(res, out, "json")[0]))
            import pandas as pd
            self.assertNotIn("Quoted as", pd.read_csv(io.BytesIO(export(res, out, "csv")[0])).columns)
            for fmt in FORMATS:
                self.assertGreater(len(export(res, out, fmt)[0]), 100)

    def test_a_failed_cost_reading_is_shown_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            r = run(d)
            res = {**load_results(r["out"]), "costs": {"currency": "INR", "rates_date": "", "vendors": [], "error": "boom"}}
            md = export(res, compute(res), "md")[0].decode("utf-8")
            self.assertIn(HEADING, md)
            self.assertIn("boom", md)


class ServiceDownloads(ApiBase):
    def test_downloads_include_the_saved_costs(self):
        self.upload_all()
        service.save_vendor_files("Priced", [("Priced.txt", io.BytesIO(
            b"Priced offer.\nPrice: INR 9,00,000 per year.\nContract term: 3 years.\n"))])
        job = service.start_evaluation(mock=True, targets={"T6": 99.9, "T7": 6}, cost_currency="INR")
        self.assertEqual(wait_done(job["id"])["status"], "done")
        for fmt in ("md", "json", "csv", "docx", "pdf", "xlsx"):
            data, name, _ = service.download(job["id"], fmt)
            self.assertGreater(len(data), 500, fmt)
        self.assertIn("2,700,000 INR", service.download(job["id"], "md")[0].decode("utf-8"))
        wb = openpyxl.load_workbook(io.BytesIO(service.download(job["id"], "xlsx")[0]))
        self.assertIn("Costs", wb.sheetnames)

    def test_older_evaluation_downloads_are_unchanged(self):
        eval_id = self.run_mock()
        md = service.download(eval_id, "md")[0].decode("utf-8")
        self.assertNotIn(HEADING, md)


if __name__ == "__main__":
    unittest.main()
