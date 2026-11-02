"""Tests for api/service.py (the logic behind the API). Uses offline --mock scoring, so no key or network."""
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from api import service
from api.service import ServiceError

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "template" / "scorecard_template.xlsx"
RFP = b"RFP: go live within 6 months. Availability at least 99.9%."
PROPOSALS = {
    "Alpha Systems": "Alpha: REST APIs, role based access control, encryption at rest, ISO 27001 certified audit logging. "
                     "Modular scalable architecture. Go-live in 5 months. Uptime 99.95%.",
    "Beta": "Beta: modular architecture and APIs. Encryption and access control. Go-live in 9 months.",
    "Gamma Tech": "Gamma Tech: generic platform. Go-live 14 months.",
    "Delta": "Delta Ltd: functional coverage, support 24x7, training included. Go-live in 6 months.",
}


def wait_done(eval_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = service.get_status(eval_id)
        if st["status"] in ("done", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError("evaluation did not finish")


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = mock.patch.dict(os.environ, {"VEA_DATA_DIR": self.tmp.name,
                                                "VEA_TEMPLATE": str(Path(self.tmp.name) / "tpl.xlsx")})
        self.env.start()
        shutil.copy2(TEMPLATE, Path(self.tmp.name) / "tpl.xlsx")
        service._results_cache.clear(); service._search_cache.clear()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def upload_all(self):
        service.save_rfp("RFP.txt", io.BytesIO(RFP))
        for v, text in PROPOSALS.items():
            service.save_vendor_files(v, [(f"{v}.txt", io.BytesIO(f"=== Page 1 ===\n{text}\n".encode()))])

    def run_mock(self):
        self.upload_all()
        job = service.start_evaluation(mock=True, targets={"T6": 99.9, "T7": 6})
        st = wait_done(job["id"])
        self.assertEqual(st["status"], "done", st.get("error"))
        return job["id"]


class Uploads(ApiBase):
    def test_vendor_files_accumulate_and_validate(self):
        r = service.save_vendor_files("Orion", [("a.txt", io.BytesIO(b"x")), ("b.pdf", io.BytesIO(b"%PDF"))])
        self.assertEqual(r["files"], ["a.txt", "b.pdf"])
        service.save_vendor_files("orion", [("c.docx", io.BytesIO(b"x"))])            # same vendor, any capitalisation
        self.assertEqual(len(service.list_vendors()), 1)
        self.assertEqual(len(service.list_vendors()[0]["files"]), 3)
        for bad_name in ("../x", "a/b", "", "x" * 90, ".hidden"):
            with self.assertRaises(ServiceError) as cm:
                service.save_vendor_files(bad_name, [("a.txt", io.BytesIO(b"x"))])
            self.assertEqual(cm.exception.status, 400)
        for bad_file in ("a.exe", "old.ppt", "~$tmp.docx", "noext"):
            with self.assertRaises(ServiceError):
                service.save_vendor_files("Orion", [(bad_file, io.BytesIO(b"x"))])
        with self.assertRaises(ServiceError):
            service.save_vendor_files("Orion", [("empty.txt", io.BytesIO(b""))])
        self.assertEqual(len(service.list_vendors()[0]["files"]), 3)                  # nothing bad was saved

    def test_path_tricks_in_filename_are_neutralised(self):
        service.save_vendor_files("Orion", [("../../evil.txt", io.BytesIO(b"x"))])
        self.assertEqual([f["file"] for f in service.list_vendors()[0]["files"]], ["evil.txt"])
        self.assertFalse((Path(self.tmp.name) / "evil.txt").exists())

    def test_delete_vendor_and_file_and_rfp(self):
        service.save_vendor_files("Orion", [("a.txt", io.BytesIO(b"x")), ("b.txt", io.BytesIO(b"x"))])
        service.delete_vendor_file("Orion", "a.txt")
        self.assertEqual(len(service.list_vendors()[0]["files"]), 1)
        service.delete_vendor_file("Orion", "b.txt")
        self.assertEqual(service.list_vendors(), [])                                 # empty vendor disappears
        service.save_rfp("r.txt", io.BytesIO(b"x")); service.save_rfp("r2.docx", io.BytesIO(b"y"))
        self.assertEqual(service.rfp_info()["file"], "r2.docx")                      # one RFP at a time
        service.delete_rfp()
        self.assertIsNone(service.rfp_info())
        with self.assertRaises(ServiceError) as cm:
            service.delete_vendor("Nobody")
        self.assertEqual(cm.exception.status, 404)

    def test_cannot_start_without_vendors(self):
        with self.assertRaises(ServiceError) as cm:
            service.start_evaluation(mock=True)
        self.assertEqual(cm.exception.status, 400)

    def test_real_run_needs_api_key(self):
        self.upload_all()
        with mock.patch.dict(os.environ, {"LLM_API_KEY": ""}):
            with self.assertRaises(ServiceError) as cm:
                service.start_evaluation(mock=False)
        self.assertIn("LLM_API_KEY", cm.exception.message)

    def test_bad_evaluation_ids_are_rejected(self):
        for bad in ("../../etc", "nope", "20261023-101530-zzzzzz"):
            with self.assertRaises(ServiceError) as cm:
                service.get_status(bad)
            self.assertEqual(cm.exception.status, 404)


class Evaluation(ApiBase):
    def test_full_flow_four_vendors(self):
        eid = self.run_mock()
        st = service.get_status(eid)
        self.assertEqual((st["percent"], len(st["vendors"])), (100, 4))
        self.assertTrue(any("Alpha Systems" in line for line in st["log"]))
        r = service.get_ranking(eid)
        self.assertEqual({x["vendor"] for x in r["ranking"]}, set(PROPOSALS))
        self.assertIn("Functionality", r["categories"])
        for cat in r["categories"].values():                                           # no 3.1800000000000006 noise
            for val in cat.values():
                self.assertTrue(val is None or round(val, 2) == val)
        self.assertEqual(r["settings"]["min_score"], 3)
        # what-if: minimum 0 means nobody fails a mandatory criterion
        r0 = service.get_ranking(eid, min_score=0)
        self.assertTrue(all(x["status"] == "Pass" for x in r0["ranking"]))
        self.assertEqual(sorted(x["rank"] for x in r0["ranking"]), [1, 2, 3, 4])
        with self.assertRaises(ServiceError):
            service.get_ranking(eid, min_score=9)
        listed = service.list_evaluations()
        self.assertEqual([e["id"] for e in listed], [eid])

    def test_vendor_detail_has_subscores_and_evidence(self):
        eid = self.run_mock()
        d = service.get_vendor(eid, "alpha systems")                                  # case-insensitive
        self.assertEqual(d["vendor"], "Alpha Systems")
        t4 = next(c for c in d["criteria"] if c["id"] == "T4")
        self.assertEqual(len(t4["sub_criteria"]), 4)
        sub = t4["sub_criteria"][0]
        self.assertIn("justification", sub)
        self.assertFalse(sub["overridden"])
        self.assertEqual(d["files"], ["Alpha Systems.txt"])
        json.dumps(d)                                                                 # must be JSON-serialisable (no NaN)
        with self.assertRaises(ServiceError):
            service.get_vendor(eid, "Nobody")

    def test_override_changes_scores_and_is_audited(self):
        eid = self.run_mock()
        before = service.get_vendor(eid, "Beta")
        t1 = next(c for c in before["criteria"] if c["id"] == "T1")
        res = service.override_score(eid, "t1.1", "Beta", 5, "checked the proposal by hand")
        self.assertEqual(res["new_score"], 5.0)
        after = service.get_vendor(eid, "Beta")
        t1_after = next(c for c in after["criteria"] if c["id"] == "T1")
        sub = t1_after["sub_criteria"][0]
        self.assertEqual((sub["score"], sub["overridden"]), (5.0, True))
        self.assertGreaterEqual(t1_after["score"], t1["score"])                      # T1 average follows the sub-score
        res = service.override_score(eid, "T4", "Beta", 5)                            # whole-criterion override
        self.assertIn("instead of the average", res["note"])
        self.assertEqual(next(c for c in service.get_vendor(eid, "Beta")["criteria"] if c["id"] == "T4")["score"], 5.0)
        log = service.get_overrides(eid)
        self.assertEqual([(o["id"], o["new_score"]) for o in log], [("T1.1", 5.0), ("T4", 5.0)])
        self.assertEqual(log[0]["reason"], "checked the proposal by hand")
        # the workbook itself carries the change, and the agent's original score is still in the log sheet
        wb = load_workbook(Path(self.tmp.name) / "evaluations" / eid / "output" / "scorecard.xlsx")
        self.assertIn("Agent_Log", wb.sheetnames)
        for bad in (("T99", "Beta", 3), ("T1", "Nobody", 3), ("T1", "Beta", 7)):
            with self.assertRaises(ServiceError):
                service.override_score(eid, *bad)

    def test_downloads_all_formats(self):
        eid = self.run_mock()
        for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK"), ("xlsx", b"PK"), ("csv", b"Rank"),
                           ("json", b"{"), ("md", b"# Vendor")):
            data, name, mime = service.download(eid, fmt)
            self.assertTrue(data.startswith(magic), fmt)
            self.assertTrue(name.startswith(eid), fmt)
        with self.assertRaises(ServiceError) as cm:
            service.download(eid, "exe")
        self.assertEqual(cm.exception.status, 400)

    def test_results_not_available_until_done(self):
        self.upload_all()
        with mock.patch.object(service._executor, "submit"):                          # never actually runs
            job = service.start_evaluation(mock=True)
        self.assertEqual(job["status"], "queued")
        with self.assertRaises(ServiceError) as cm:
            service.get_ranking(job["id"])
        self.assertEqual(cm.exception.status, 409)

    def test_missing_target_fails_with_clear_message_and_never_waits_for_keyboard(self):
        service.save_vendor_files("Orion", [("o.txt", io.BytesIO(b"=== Page 1 ===\nOrion text about APIs.\n"))])
        with mock.patch("builtins.input", side_effect=AssertionError("must not ask for input")):
            job = service.start_evaluation(mock=True)                                  # no RFP, no targets
            st = wait_done(job["id"])
        self.assertEqual(st["status"], "failed")
        self.assertIn("No target found", st["error"])

    def test_restart_marks_unfinished_runs_failed(self):
        self.upload_all()
        with mock.patch.object(service._executor, "submit"):
            job = service.start_evaluation(mock=True)
        service.recover()
        st = service.get_status(job["id"])
        self.assertEqual(st["status"], "failed")
        self.assertIn("restarted", st["error"])


class WindowsFileLocking(ApiBase):
    """Windows raises PermissionError when a file is replaced while another thread reads it."""

    def test_job_file_write_retries_on_permission_error(self):
        self.upload_all()
        with mock.patch.object(service._executor, "submit"):
            job = service.start_evaluation(mock=True)
        real_replace, calls = os.replace, {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 3:
                raise PermissionError("WinError 32: file in use")
            return real_replace(src, dst)
        with mock.patch("api.service.os.replace", flaky):
            service._update_job(job["id"], message="hello")
        self.assertEqual(service.get_status(job["id"])["message"], "hello")
        self.assertGreater(calls["n"], 3)

    def test_job_file_read_retries_on_permission_error(self):
        self.upload_all()
        with mock.patch.object(service._executor, "submit"):
            job = service.start_evaluation(mock=True)
        real_read, calls = Path.read_text, {"n": 0}

        def flaky(self, *a, **k):
            if self.name == "job.json":
                calls["n"] += 1
                if calls["n"] <= 3:
                    raise PermissionError("WinError 32: file in use")
            return real_read(self, *a, **k)
        with mock.patch.object(Path, "read_text", flaky):
            self.assertEqual(service.get_status(job["id"])["status"], "queued")

    def test_polling_while_job_updates_never_fails(self):
        import threading
        self.upload_all()
        with mock.patch.object(service._executor, "submit"):
            job = service.start_evaluation(mock=True)
        errors, stop = [], threading.Event()

        def writer():
            for i in range(300):
                try:
                    service._update_job(job["id"], percent=i % 100, log_line=f"step {i}")
                except Exception as e:
                    errors.append(e)
            stop.set()
        th = threading.Thread(target=writer); th.start()
        while not stop.is_set():
            try:
                service.get_status(job["id"])
            except Exception as e:
                errors.append(e)
        th.join()
        self.assertEqual(errors, [])

    def test_override_does_not_deadlock_with_status_lock(self):
        eid = self.run_mock()
        service.override_score(eid, "T1", "Beta", 4)                                   # locks, then reads status inside
        self.assertEqual(service.get_status(eid)["status"], "done")


class Chat(ApiBase):
    def test_chat_gets_facts_and_proposal_passages(self):
        eid = self.run_mock()
        seen = {}

        def fake_llm(messages, retries=3):
            seen["system"] = messages[0]["content"]; seen["last"] = messages[-1]["content"]
            return "Alpha ranks well. [Alpha Systems, T4]"
        with mock.patch("vea.chat._call_llm", fake_llm):
            r = service.chat_about(eid, "Does Alpha Systems have ISO 27001?", [{"role": "user", "content": "hi"}])
        self.assertIn("Alpha ranks well", r["answer"])
        self.assertIn("RANKING", seen["system"])
        self.assertIn("PROPOSAL PASSAGES", seen["system"])                            # search over the saved chunks
        self.assertIn("ISO 27001", seen["system"])
        self.assertIn(r["proposal_search"], ("semantic", "keyword"))
        with self.assertRaises(ServiceError):
            service.chat_about(eid, "   ")

    def test_chat_model_errors_become_502(self):
        eid = self.run_mock()
        from vea.chat import ChatError
        with mock.patch("vea.chat._call_llm", side_effect=ChatError("Rate limit reached.")):
            with self.assertRaises(ServiceError) as cm:
                service.chat_about(eid, "Why is Beta ranked last?")
        self.assertEqual(cm.exception.status, 502)


class Criteria(ApiBase):
    def test_get_and_update_criteria(self):
        c = service.get_criteria()
        self.assertEqual(len(c["criteria"]), 9)
        t4 = next(x for x in c["criteria"] if x["id"] == "T4")
        self.assertEqual(len(t4["sub_criteria"]), 4)
        subs = [s for x in c["criteria"] for s in x["sub_criteria"]]
        for s in subs:                                                                # re-weight T4 subs 40/30/20/10
            if s["parent_id"] == "T4":
                s["weight"] = {"T4.1": 0.4, "T4.2": 0.3, "T4.3": 0.2, "T4.4": 0.1}[s["id"]]
        new = service.update_criteria([{"id": "T4", "description": "Security controls and certifications"}], subs)
        t4 = next(x for x in new["criteria"] if x["id"] == "T4")
        self.assertEqual([s["weight"] for s in t4["sub_criteria"]], [0.4, 0.3, 0.2, 0.1])
        self.assertEqual(t4["description"], "Security controls and certifications")
        self.assertTrue((Path(self.tmp.name) / "tpl.bak.xlsx").exists())              # previous version kept

    def test_invalid_edits_are_rejected_and_template_untouched(self):
        before = (Path(self.tmp.name) / "tpl.xlsx").read_bytes()
        with self.assertRaises(ServiceError) as cm:
            service.update_criteria([{"id": "T4", "weight": 0.9}])                    # weights no longer total 100%
        self.assertIn("100%", cm.exception.message)
        with self.assertRaises(ServiceError):
            service.update_criteria([{"id": "T99", "weight": 0.1}])
        with self.assertRaises(ServiceError):
            service.update_criteria([{"id": "T4", "colour": "red"}])
        bad_subs = [{"id": "T4.1", "parent_id": "T4", "name": "X", "weight": 0.5}]    # only 50% of T4
        with self.assertRaises(ServiceError):
            service.update_criteria(None, bad_subs)
        numeric_sub = [{"id": "T7.1", "parent_id": "T7", "name": "X", "weight": 1.0}]
        with self.assertRaises(ServiceError) as cm:
            service.update_criteria(None, numeric_sub)
        self.assertIn("cannot have sub-criteria", cm.exception.message)
        self.assertEqual((Path(self.tmp.name) / "tpl.xlsx").read_bytes(), before)
        self.assertFalse(list(Path(self.tmp.name).glob("*.editing.xlsx")))            # temp copy cleaned up

    def test_removing_all_subcriteria_and_new_run_uses_edited_criteria(self):
        service.update_criteria(None, [])                                             # empty list = no sub-criteria
        self.assertTrue(all(not c["sub_criteria"] for c in service.get_criteria()["criteria"]))
        eid = self.run_mock()
        d = service.get_vendor(eid, "Beta")
        self.assertTrue(all(not c["sub_criteria"] for c in d["criteria"]))
        service.update_criteria(None, None)                                           # None = leave unchanged


if __name__ == "__main__":
    unittest.main()
