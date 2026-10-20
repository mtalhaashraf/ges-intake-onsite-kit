import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ges_intake.__main__ import main
from ges_intake.govern import PARSERS, name_tokens, parse_date

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ("900-12-3456", "900123456", "900-98-7654", "58-1234567", "0571 2290 4410", "5512 90871",
           "0004 5512 4821", "77-0019930")


class EngineCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_inbox.py"), str(cls.tmp / "inbox")],
                       check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def invoke(self, *inboxes, client="C-1001", as_of="2026-03-01", library=None):
        outbox = self.tmp / "outbox.jsonl"
        argv = ["--library", str(library or ROOT / "library"), "--clients", str(ROOT / "clients"), "run",
                "--client", client, "--as-of", as_of, "--outbox", str(outbox)]
        for inbox in inboxes:
            argv += ["--inbox", str(inbox)]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def report(self, *days, as_of="2026-03-01"):
        code, out, _ = self.invoke(*(self.tmp / "inbox" / d for d in days), as_of=as_of)
        self.assertEqual(code, 0)
        return json.loads(out), out

    def test_day1_counts(self):
        batch = self.report("day1")[0]["batches"][0]
        self.assertEqual((len(batch["documents"]), len(batch["duplicates"]), len(batch["rejected"])), (20, 1, 8))
        self.assertEqual(batch["ignored"], ["notes_unlisted.txt"])
        reasons = {r["source"]: r["reason"] for r in batch["rejected"]}
        self.assertEqual(reasons["../outside_secret.txt"], "unsafe_path")
        self.assertEqual(reasons["dl_scan.txt"], "invalid_timestamp")
        self.assertEqual(reasons["huge.txt"], "too_large")
        self.assertEqual(reasons["latin1.txt"], "not_utf8")

    def test_day1_readiness(self):
        readiness = self.report("day1")[0]["readiness"]
        statuses = {p["id"]: p["status"] for p in readiness["pdas"]}
        self.assertEqual(statuses, {"PDA-A01": "SATISFIED", "PDA-A02": "MISSING", "PDA-A03": "MISSING",
                                    "PDA-A04": "PENDING_REVIEW", "PDA-A05": "BLOCKED_BY_DEPENDENCY"})
        self.assertEqual(readiness["status"], "BLOCKED")
        missing = readiness["pdas"][2]["pathways"][0]["missing"]
        self.assertEqual(missing, [
            {"cel": "CEL-BNK-001", "account": "4821", "gaps": [["2025-03-01", "2025-03-31"]]},
            {"cel": "CEL-BNK-001", "account": "9930", "gaps": [["2025-02-28", "2025-02-28"]]}])
        self.assertIn("balance_discontinuity:9930:2025-05-01", readiness["pdas"][2]["issues"])

    def test_day2_supersedes_and_fills_gaps(self):
        report = self.report("day1", "day2", as_of="2026-03-03")[0]
        statuses = {p["id"]: p["status"] for p in report["readiness"]["pdas"]}
        self.assertEqual(statuses["PDA-A02"], "SATISFIED")
        self.assertEqual(statuses["PDA-A03"], "PENDING_REVIEW")
        self.assertEqual([d["status"] for d in report["documents"]].count("superseded"), 2)
        self.assertEqual(len(report["batches"][1]["duplicates"]), 1)

    def test_no_identifier_leaks_and_advisory_never_applied(self):
        report, out = self.report("day1")
        outbox = (self.tmp / "outbox.jsonl").read_text(encoding="utf-8")
        for secret in SECRETS:
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, outbox)
        payroll = next(d for d in report["batches"][0]["documents"] if d["source"] == "payroll_summary_2025.txt")
        self.assertIsNone(payroll["doc_type"])
        self.assertEqual(payroll["status"], "pending_review")

    def test_client_view_has_no_codes(self):
        client = json.dumps(self.report("day1")[0]["attention"]["client"])
        for code in ("PDA-", "CEL-", "DT-", "DOC-", "quarantin", '"key"'):
            self.assertNotIn(code, client)

    def test_deterministic(self):
        self.assertEqual(self.report("day1", "day2")[1], self.report("day1", "day2")[1])

    def test_exit_2_cases(self):
        day1 = self.tmp / "inbox" / "day1"
        self.assertEqual(self.invoke(day1, client="../clients/C-1001")[0], 2)
        self.assertEqual(self.invoke(day1, client="C-9999")[0], 2)
        self.assertEqual(self.invoke(day1, client="C-2002")[0], 2)
        self.assertEqual(self.invoke(self.tmp / "inbox")[0], 2)
        bad = self.tmp / "badlib"
        shutil.copytree(ROOT / "library", bad, dirs_exist_ok=True)
        services = json.loads((bad / "services.json").read_text())
        services["services"][0]["pdas"][2]["depends_on"] = ["PDA-A05"]
        (bad / "services.json").write_text(json.dumps(services))
        code, out, err = self.invoke(day1, library=bad)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("cycle", err)


class ParserCase(unittest.TestCase):
    def test_parsers(self):
        self.assertEqual(PARSERS["money"]("($45.20)"), Decimal("-45.20"))
        self.assertEqual(PARSERS["money"]("$1,000.1"), Decimal("1000.10"))
        self.assertIsNone(PARSERS["money"]("1,00.00"))
        self.assertIsNone(PARSERS["money"]("(-5.00)"))
        self.assertEqual(PARSERS["ssn"]("XXX-XX-3456"), "***-**-3456")
        self.assertEqual(PARSERS["ein"]("58-1234567"), "**-***4567")
        self.assertEqual(PARSERS["account"]("77-0019930"), "9930")
        self.assertEqual(parse_date("May 14, 2031"), date(2031, 5, 14))
        self.assertEqual(name_tokens("RIVERA, JORDAN Q"), name_tokens("Jordan Q. Rivera"))


if __name__ == "__main__":
    os.chdir(ROOT)
    unittest.main()
