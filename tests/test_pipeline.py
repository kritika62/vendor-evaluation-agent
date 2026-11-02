import tempfile
import unittest
from pathlib import Path

from vea.aggregate import aggregate
from vea.config import Settings
from vea.ingest import chunk_pages, group_vendors, ingest_files, read_pages
from vea.retriever import Retriever
from vea.rubric import load_rubric
from vea.numeric import convert, score_higher_is_better, score_lower_is_better
from vea.scorer import CriterionScore, MockLLM, parse_json, score_criterion, score_numeric, score_with_subs, validate

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "scorecard_template.xlsx"


ALPHA_TEXT = ("=== Page 1 ===\nAlpha Systems covers all functional requirements. Modular architecture. Go-live in 9 months.\n"
              "=== Page 2 ===\nSecurity: ISO 27001 certified, encryption at rest and in transit, role based access "
              "control and audit logging.\n=== Page 3 ===\nUptime 99.5%. Support during business hours only.\n")


def alpha_pages():
    """A small built-in 3-page sample, so the tests do not depend on whatever is in data/proposals."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "Alpha_Systems.txt"
        f.write_text(ALPHA_TEXT, encoding="utf-8")
        return read_pages(f)


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def complete_json(self, *a, **k):
        return dict(self.payload)


class Tests(unittest.TestCase):
    def test_rubric_loads_and_weights_sum_to_one(self):
        criteria, min_s, n = load_rubric(str(TEMPLATE))
        self.assertEqual(len(criteria), 9)
        self.assertAlmostEqual(sum(c.weight for c in criteria), 1.0)
        self.assertEqual((min_s, n), (3, 2))

    def test_page_markers_and_chunk_page_tags(self):
        pages = alpha_pages()
        self.assertEqual([p for p, _ in pages], [1, 2, 3])
        chunks = chunk_pages("Alpha", pages)
        self.assertTrue(all(c.page in (1, 2, 3) for c in chunks))

    def test_validate_rejects_bad_scores(self):
        good = {"addressed": True, "score": 4, "confidence": "high", "justification": "x",
                "evidence_quote": "q", "pages": [1]}
        self.assertEqual(validate(dict(good))["score"], 4)
        for bad in ({"score": 7}, {"score": "4"}, {"confidence": "sure"}, {"pages": ["1"]}):
            with self.assertRaises(ValueError):
                validate({**good, **bad})

    def test_parse_json_handles_code_fences(self):
        self.assertEqual(parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_mandatory_knockout_and_ranking(self):
        criteria, min_s, n = load_rubric(str(TEMPLATE))

        def mk(vendor, scores):
            return [CriterionScore(vendor, c.id, c.name, s, "high", "", "") for c, s in zip(criteria, scores)]
        data = {"A": mk("A", [4, 4, 3, 5, 4, 4, 3, 4, 3]),   # passes, 3.90
                "B": mk("B", [5, 3, 4, 3, 3, 3, 4, 3, 4]),   # passes, 3.65
                "C": mk("C", [3, 5, 5, 2, 4, 5, 3, 3, 5])}   # fails T4 despite 3.75
        rows = {r["vendor"]: r for r in aggregate(criteria, data, min_s, n)}
        self.assertAlmostEqual(rows["A"]["weighted_score"], 3.90)
        self.assertEqual(rows["C"]["status"], "Fail")
        self.assertEqual(rows["C"]["recommendation"], "Rejected - mandatory criterion not met")
        self.assertEqual((rows["A"]["rank"], rows["B"]["rank"]), (1, 2))

    def test_hallucinated_quote_is_flagged(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        s = Settings(mock=True)
        pages = alpha_pages()
        retriever = Retriever(chunk_pages("Alpha", pages), s)
        sec = next(c for c in criteria if c.id == "T4")
        fake = FakeLLM({"addressed": True, "score": 5, "confidence": "high", "justification": "great",
                        "evidence_quote": "We invented this sentence that is not in the proposal", "pages": [2]})
        s.mock = False  # use the LLM path with the fake client (retriever already built offline)
        r = score_criterion("Alpha", sec, retriever, fake, s, min_s)
        self.assertTrue(r.needs_review)
        self.assertIn("quote not found", r.review_reason)

    def test_not_addressed_scores_zero(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        s = Settings(mock=True)
        retriever = Retriever(chunk_pages("V", [(1, "Completely unrelated text about catering menus.")]), s)
        r = score_criterion("V", criteria[0], retriever, MockLLM(), s, min_s)
        self.assertEqual(r.score, 0)
        self.assertTrue(r.needs_review)

    # ---- numeric scoring ----
    def test_lower_is_better_formula(self):
        # target 9 months: on target = 5, 12 months = 3.33, 18 months (2x target) = 0
        self.assertEqual(score_lower_is_better(9, 9), 5)
        self.assertAlmostEqual(score_lower_is_better(12, 9), 3.333, places=2)
        self.assertEqual(score_lower_is_better(18, 9), 0)
        self.assertEqual(score_lower_is_better(30, 9), 0)      # capped at 0
        self.assertEqual(score_lower_is_better(6, 9), 5)       # early is capped at 5
        self.assertAlmostEqual(score_lower_is_better(14, 12), 4.167, places=2)

    def test_higher_is_better_formula(self):
        self.assertAlmostEqual(score_higher_is_better(99.5, 99.9, 95), 4.59, places=2)
        self.assertEqual(score_higher_is_better(99.99, 99.9, 95), 5)
        self.assertEqual(score_higher_is_better(90, 99.9, 95), 0)

    def test_unit_conversion(self):
        self.assertAlmostEqual(convert(2, "years", "months"), 24)
        self.assertAlmostEqual(convert(1.5, "year", "months"), 18)
        self.assertAlmostEqual(convert(26, "weeks", "months"), 6)
        with self.assertRaises(ValueError):
            convert(5, "hours", "months")

    def test_numeric_criterion_end_to_end_with_mock(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t7 = next(c for c in criteria if c.id == "T7")
        self.assertEqual(t7.method, "Lower is better")
        s = Settings(mock=True)
        pages = [(1, "Implementation\nThe project will be delivered in a 9-month implementation plan.")]
        retriever = Retriever(chunk_pages("V", pages), s)
        r = score_numeric("V", t7, retriever, MockLLM(), s, {"value": 6.0, "unit": "months"}, min_s)
        self.assertEqual(r.score, 2.5)                        # 5 x (12 - 9) / 6
        self.assertIn("Score = 5 x (2 x 6 - 9.0) / 6", r.justification)

    def test_missing_value_scores_zero_and_is_flagged(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t7 = next(c for c in criteria if c.id == "T7")
        s = Settings(mock=True)
        retriever = Retriever(chunk_pages("V", [(1, "Implementation\nWe will implement it carefully.")]), s)
        r = score_numeric("V", t7, retriever, MockLLM(), s, {"value": 6.0, "unit": "months"}, min_s)
        self.assertEqual(r.score, 0)
        self.assertIn("value not stated", r.review_reason)

    # ---- several vendors, several formats ----
    def test_group_vendors_folders_prefixes_and_per_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "Orion").mkdir()
            for n in ("Orion/a.pdf", "Orion/b.docx", "Beta__tech.txt", "Beta__price.xlsx", "Solo_Corp.pptx", "~$tmp.docx", "notes.md"):
                (d / n).write_bytes(b"x")
            g = group_vendors(d)
            self.assertEqual(sorted(g), ["Beta", "Orion", "Solo Corp"])
            self.assertEqual(len(g["Orion"]), 2)
            self.assertEqual(len(g["Beta"]), 2)
            self.assertEqual(len(group_vendors(d, per_file=True)), 5)        # every file is its own vendor

    def test_ingest_mixed_formats_tags_source_file(self):
        import docx, pptx
        from openpyxl import Workbook
        from pptx.util import Inches
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            doc = docx.Document(); doc.add_paragraph("Encryption uses AES-256."); doc.save(d / "a.docx")
            pr = pptx.Presentation(); sl = pr.slides.add_slide(pr.slide_layouts[5])
            sl.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "Go-live in 5 months"
            pr.save(d / "b.pptx")
            wb = Workbook(); wb.active.append(["Uptime", "99.95%"]); wb.save(d / "c.xlsx")
            (d / "d.txt").write_text("Support is 24x7", encoding="utf-8")
            chunks = ingest_files("V", sorted(d.iterdir()))
        self.assertEqual({c.source for c in chunks}, {"a.docx", "b.pptx", "c.xlsx", "d.txt"})
        self.assertTrue(any("99.95%" in c.text and c.source == "c.xlsx" for c in chunks))

    # ---- sub-criteria ----
    def test_subcriteria_load_with_weights_summing_to_one(self):
        criteria, _, _ = load_rubric(str(TEMPLATE))
        t4 = next(c for c in criteria if c.id == "T4")
        self.assertEqual([s.id for s in t4.subs], ["T4.1", "T4.2", "T4.3", "T4.4"])
        self.assertAlmostEqual(sum(s.weight for s in t4.subs), 1.0)
        self.assertEqual(next(c for c in criteria if c.id == "T7").subs, [])      # numeric: no subs

    def test_subcriteria_validation_errors(self):
        from openpyxl import load_workbook
        for edit, msg in ((lambda ws: ws.__setitem__("G5", 0.5), "must be 100%"),            # T1 weights now 50% + 30%
                          (lambda ws: ws.__setitem__("C5", "T7"), "cannot have sub-criteria"),
                          (lambda ws: ws.__setitem__("C5", "T99"), "not on the Criteria sheet")):
            with tempfile.TemporaryDirectory() as d:
                wb = load_workbook(TEMPLATE); edit(wb["SubCriteria"]); p = str(Path(d) / "t.xlsx"); wb.save(p)
                with self.assertRaises(ValueError) as cm:
                    load_rubric(p)
                self.assertIn(msg, str(cm.exception))

    def test_criterion_score_is_weighted_average_of_subscores(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t4 = next(c for c in criteria if c.id == "T4")
        text = ("Access control uses role based access control with SSO.\n\nAll data is encrypted at rest with AES-256.\n\n"
                "The company is ISO 27001 certified.\n\nAudit logs are kept for 12 months.")
        s = Settings(mock=True)
        chunks = chunk_pages("V", [(1, text)], source="v.pdf")
        retriever = Retriever(chunks, s)
        scores = iter([5, 5, 1, 1])

        class Seq:
            def complete_json(self, *a, **k):
                return {"addressed": True, "score": next(scores), "confidence": "high", "justification": "j",
                        "evidence_quote": "ISO 27001 certified", "pages": [1]}
        s.mock = False
        r = score_with_subs("V", t4, retriever, Seq(), s, min_s)
        self.assertEqual(r.score, 3.0)                              # (5+5+1+1)/4, equal weights
        self.assertEqual([x.score for x in r.subscores], [5, 5, 1, 1])
        self.assertFalse(r.needs_review)                            # 3.0 meets the mandatory minimum, quotes verified
        self.assertIn("v.pdf p.1", r.sources)                       # source file is recorded from the verified quote
        self.assertEqual(r.subscores[0].parent_id, "T4")
        scores = iter([1, 1, 1, 1])                                 # all weak: mandatory T4 falls below the minimum
        r2 = score_with_subs("V", t4, retriever, Seq(), s, min_s)
        self.assertEqual(r2.score, 1.0)
        self.assertIn("mandatory criterion below minimum", r2.review_reason)

    def test_no_subs_means_whole_criterion_scoring(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t1 = next(c for c in criteria if c.id == "T1")
        t1.subs = []
        s = Settings(mock=True)
        retriever = Retriever(chunk_pages("V", [(1, "The proposal covers all functional requirements.")]), s)
        r = score_with_subs("V", t1, retriever, MockLLM(), s, min_s)
        self.assertEqual(r.subscores, [])

    def test_sub_prompt_is_narrow_and_names_the_file(self):
        from vea.scorer import build_user_prompt
        criteria, _, _ = load_rubric(str(TEMPLATE))
        t4 = next(c for c in criteria if c.id == "T4")
        chunk = chunk_pages("V", [(2, "Data is encrypted at rest.")], source="Orion.pdf")
        p = build_user_prompt(t4.subs[1], chunk, parent=t4)
        self.assertIn("SUB-CRITERION TO SCORE: Encryption", p)
        self.assertIn("File: Orion.pdf | Page 2", p)

    # ---- numeric extraction: number buried where the first search does not look ----
    def _buried(self, sentence):
        filler = [f"Section {i}. Our implementation timeline and go-live schedule are described by programme "
                  f"governance, delivery methodology, project plan and implementation approach number {i}."
                  for i in range(30)]
        paras = filler[:15] + [sentence] + filler[15:]
        return chunk_pages("V", [(1, "\n\n".join(paras))], max_chars=300, overlap_chars=0, source="v.txt")

    def test_numeric_value_missed_by_search_is_found_by_fallback_scan(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t7 = next(c for c in criteria if c.id == "T7")
        s = Settings(mock=True)
        chunks = self._buried("The programme runs four phases across 16 weeks with named milestones.")
        retriever = Retriever(chunks, s)
        first_pass = retriever.search(f"{t7.name}. {t7.description}")
        self.assertFalse(any("16 weeks" in c.text for c in first_pass))        # the old single search misses it
        r = score_numeric("V", t7, retriever, MockLLM(), s, {"value": 6.0, "unit": "months"}, min_s)
        self.assertEqual(r.score, 5)                                           # 16 weeks = 3.7 months, under target
        self.assertIn("16", r.justification)
        self.assertEqual(r.sources, ["v.txt p.1"])
        self.assertNotIn("value not stated", r.review_reason)

    def test_numeric_value_truly_absent_is_flagged_with_clear_advice(self):
        criteria, min_s, _ = load_rubric(str(TEMPLATE))
        t7 = next(c for c in criteria if c.id == "T7")
        s = Settings(mock=True)
        retriever = Retriever(self._buried("We will deliver the programme carefully."), s)
        r = score_numeric("V", t7, retriever, MockLLM(), s, {"value": 6.0, "unit": "months"}, min_s)
        self.assertEqual(r.score, 0)
        self.assertTrue(r.needs_review)
        self.assertIn("check the proposal text by hand", r.review_reason)

    def test_unit_patterns_cover_words_and_percent(self):
        from vea.numeric import search_hints, unit_pattern
        tp, pp = unit_pattern("months"), unit_pattern("%")
        for txt in ("16 weeks", "sixteen weeks", "twenty-four months", "9-month plan", "30 business days", "1.5 years"):
            self.assertTrue(tp.search(txt), txt)
        for txt in ("99.9%", "99.95 percent", "99.9 per cent"):
            self.assertTrue(pp.search(txt), txt)
        self.assertIsNone(tp.search("version 16 of the plan"))
        self.assertIsNone(unit_pattern("widgets"))
        self.assertIn("uptime", search_hints("percent"))
        self.assertIn("weeks", search_hints("months"))

    def test_retriever_search_k_overrides_default(self):
        s = Settings(mock=True)
        r = Retriever(self._buried("Four phases across 16 weeks."), s)
        self.assertLessEqual(len(r.search("implementation timeline")), s.k_final)
        self.assertEqual(len(r.search("implementation timeline", k=7)), 7)


if __name__ == "__main__":
    unittest.main()


class CsvIngestTests(unittest.TestCase):
    def test_csv_rows_become_text_lines(self):
        import tempfile
        from pathlib import Path
        from vea.ingest import SUPPORTED, read_pages
        self.assertIn(".csv", SUPPORTED)
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "ey.csv"
            f.write_text("Section,Text\n1,We hold ISO 27001\n2,Go-live in 16 weeks\n", encoding="utf-8-sig")
            pages = read_pages(f)
        self.assertEqual(len(pages), 1)
        self.assertIn("1 | We hold ISO 27001", pages[0][1])
