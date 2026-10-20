import json
import re
import unittest

from tests.helpers import Workspace, by_source, pda

SECRETS = ["900-12-3456", "900123456", "900-98-7654", "58-1234567", "58-7654321", "58-1112223", "0004 5512",
           "77-0019930", "0019930", "3300 7777", "0571 2290", "5512 90871", "Morgan", "Ellis", "never be ingested"]


class Day1Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.result = cls.ws.run()
        cls.report = json.loads(cls.result.stdout)
        cls.batch = cls.report["batches"][0]

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_self_check_counts(self):
        self.assertEqual(self.result.returncode, 0)
        self.assertEqual((len(self.batch["documents"]), len(self.batch["duplicates"]), len(self.batch["rejected"]),
                          len(self.batch["ignored"])), (20, 1, 8, 1))
        self.assertEqual(self.batch["duplicates"][0]["source"], "w2_acme_2025_copy.txt")

    def test_self_check_readiness(self):
        statuses = {p["id"]: p["status"] for p in self.report["readiness"]["pdas"]}
        self.assertEqual(statuses, {"PDA-A01": "SATISFIED", "PDA-A02": "MISSING", "PDA-A03": "MISSING",
                                    "PDA-A04": "PENDING_REVIEW", "PDA-A05": "BLOCKED_BY_DEPENDENCY"})
        self.assertEqual(self.report["readiness"]["status"], "BLOCKED")
        quarantined = by_source(self.batch, "int_peach_cu_2025.txt")
        self.assertEqual(self.report["readiness"]["security_holds"], [f"document:{quarantined['document_id']}"])

    def test_bank_gaps_per_account(self):
        missing = pda(self.report, "PDA-A03")["pathways"][0]["missing"]
        self.assertEqual(missing, [
            {"cel": "CEL-BNK-001", "account": "4821", "gaps": [["2025-03-01", "2025-03-31"]]},
            {"cel": "CEL-BNK-001", "account": "9930", "gaps": [["2025-02-28", "2025-02-28"]]},
        ])
        self.assertIn("balance_discontinuity:9930:2025-05-01", pda(self.report, "PDA-A03")["issues"])

    def test_governance_of_tricky_documents(self):
        transcript = by_source(self.batch, "irs_transcript_2024.txt")
        self.assertEqual((transcript["provenance"], transcript["grade"]), ("P3", "E3"))
        self.assertIn("claimed_official_source_unverified", transcript["flags"])

        injected = by_source(self.batch, "nec_brightpath_2025.txt")
        self.assertEqual((injected["doc_type"], injected["status"]), ("DT-1099-NEC", "pending_review"))
        self.assertIn("suspicious_instructions", injected["flags"])

        other = by_source(self.batch, "int_peach_cu_2025.txt")
        self.assertEqual((other["status"], other["grade"], other["provenance"], other["fields"]),
                         ("quarantined", "E5", None, {}))

        ambiguous = by_source(self.batch, "payroll_summary_2025.txt")
        self.assertEqual([ambiguous[k] for k in ("doc_type", "cel", "provenance", "grade", "status")],
                         [None, None, None, None, "pending_review"])
        self.assertEqual(ambiguous["flags"], ["ambiguous_type"])
        self.assertFalse(ambiguous["advisory"]["applied"])

        stray = by_source(self.batch, "stmt_7777_2025_01.txt")
        self.assertEqual((stray["grade"], stray["flags"]), ("E4", ["name_mismatch", "unknown_account"]))

        w2 = by_source(self.batch, "w2_acme_2025.txt")
        self.assertEqual(w2["fields"]["employee_ssn"], "***-**-3456")
        self.assertEqual(w2["fields"]["employer_ein"], "**-***4567")
        self.assertEqual(w2["fields"]["wages"], "48250.00")
        self.assertEqual(by_source(self.batch, "stmt_9930_2025_06.txt")["fields"]["ending_balance"], "-45.20")

    def test_no_identifier_leaves_the_vault(self):
        for text in (self.result.stdout, self.result.stderr, self.ws.outbox_text()):
            for secret in SECRETS:
                self.assertNotIn(secret, text)

    def test_advisory_called_once_per_unclassified_document_with_redacted_text(self):
        lines = [json.loads(line) for line in self.ws.outbox_text().splitlines()]
        self.assertEqual(len(lines), 1)
        self.assertIn("Year-End Payroll", lines[0]["text"])
        self.assertNotIn("Rivera", lines[0]["text"])

    def test_client_attention_leaks_nothing_internal(self):
        client = self.report["attention"]["client"]
        self.assertEqual([i["level"] for i in client], ["NOW", "NOW"])
        text = json.dumps(client)
        self.assertNotRegex(text, r"(CEL|PDA|DT|DOC)-|\b[PECS][1-5]\b|\bR[UV][0-9]\b|quarantin|key")
        self.assertIn("ending 4821", text)
        self.assertIn("2025-03-01 to 2025-03-31", text)
        owner = self.report["attention"]["owner"]
        self.assertEqual([i["kind"] for i in owner], ["security_incident"])


class Day2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Workspace()
        cls.forward = cls.ws.report("day1", "day2", as_of="2026-03-03")
        cls.backward = cls.ws.report("day2", "day1", as_of="2026-03-03")

    @classmethod
    def tearDownClass(cls):
        cls.ws.close()

    def test_corrections_supersede_older_versions(self):
        day1, day2 = self.forward["batches"]
        final = {d["document_id"]: d["status"] for d in self.forward["documents"]}
        old_w2 = by_source(day1, "w2_acme_2025.txt")["document_id"]
        old_feb = by_source(day1, "stmt_9930_2025_02.txt")["document_id"]
        self.assertEqual(by_source(day1, "w2_acme_2025.txt")["status"], "accepted")  # as of the end of day 1
        self.assertEqual((final[old_w2], final[old_feb]), ("superseded", "superseded"))
        self.assertEqual(by_source(day2, "w2c_acme_2025.txt")["status"], "accepted")
        self.assertEqual([d["source"] for d in day2["duplicates"]], ["engagement_letter.txt"])

    def test_ingest_order_does_not_change_the_outcome(self):
        self.assertEqual(self.forward["documents"], self.backward["documents"])
        self.assertEqual(self.forward["readiness"], self.backward["readiness"])
        day2_first, day1_second = self.backward["batches"]
        self.assertEqual(by_source(day1_second, "w2_acme_2025.txt")["status"], "superseded")

    def test_readiness_after_day2(self):
        statuses = {p["id"]: p["status"] for p in self.forward["readiness"]["pdas"]}
        self.assertEqual(statuses, {"PDA-A01": "SATISFIED", "PDA-A02": "SATISFIED", "PDA-A03": "PENDING_REVIEW",
                                    "PDA-A04": "PENDING_REVIEW", "PDA-A05": "BLOCKED_BY_DEPENDENCY"})
        a03 = pda(self.forward, "PDA-A03")
        self.assertTrue(a03["pathways"][0]["met"])
        self.assertEqual(len(a03["issues"]), 2)
        self.assertEqual(a03["issues"][0], "balance_discontinuity:9930:2025-05-01")
        self.assertEqual(self.forward["readiness"]["status"], "BLOCKED")


class DeterminismTest(unittest.TestCase):
    def test_identical_bytes_in_any_time_zone_and_locale(self):
        ws = Workspace()
        try:
            outputs = {ws.run("day1", "day2", env={"TZ": tz, "LC_ALL": loc}).stdout
                       for tz, loc in [("UTC", "C"), ("Asia/Karachi", "C"), ("America/New_York", "fr_FR.UTF-8")]}
            self.assertEqual(len(outputs), 1)
        finally:
            ws.close()


class ExitTwoTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()

    def tearDown(self):
        self.ws.close()

    def assertExitTwo(self, *inboxes, **kwargs):
        result = self.ws.run(*inboxes, **kwargs)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertTrue(result.stderr.strip())
        self.assertFalse(self.ws.outbox.exists(), "nothing may be sent when the run is refused")
        return result

    def test_missing_and_malformed_manifest(self):
        self.assertExitTwo("day1", "nowhere")
        (self.ws.inbox / "day2" / "manifest.json").write_text("{not json")
        self.assertExitTwo("day1", "day2")
        (self.ws.inbox / "day2" / "manifest.json").write_text('{"client_id": "C-1001", "batch_id": "B"}')
        self.assertExitTwo("day1", "day2")

    def test_manifest_for_another_client(self):
        self.ws.edit_json(self.ws.inbox / "day2" / "manifest.json", lambda m: m.update(client_id="C-2002"))
        self.assertExitTwo("day1", "day2")

    def test_bad_client_ids(self):
        for client in ["../clients/C-1001", "C-1001.json", "C-9999", "c-1001", "", "C-1001/", "C 1001"]:
            with self.subTest(client=client):
                self.assertExitTwo(client=client)

    def test_unknown_reference_in_library(self):
        def change(services):
            services["services"][0]["pdas"][0]["pathways"][0]["requirements"][0]["cel"] = "CEL-NOPE-001"
        self.ws.edit_json(self.ws.library / "services.json", change)
        self.assertIn("CEL-NOPE-001", self.assertExitTwo().stderr)

    def test_dependency_cycle(self):
        def change(services):
            services["services"][0]["pdas"][2]["depends_on"] = ["PDA-A05"]
        self.ws.edit_json(self.ws.library / "services.json", change)
        self.assertIn("cycle", self.assertExitTwo().stderr)

    def test_unknown_supersedes_and_bad_regex(self):
        self.ws.edit_json(self.ws.library / "document_types.json",
                          lambda d: d["document_types"][5]["supersede"].update(supersedes=["DT-NOPE"]))
        self.assertExitTwo()

    def test_bad_regex(self):
        self.ws.edit_json(self.ws.library / "document_types.json",
                          lambda d: d["document_types"][0]["fields"]["client_name"].update(pattern="(unclosed"))
        self.assertExitTwo()


class DataDrivenTest(unittest.TestCase):
    """Rename every ID in the library: the engine must behave the same, so nothing is hard-coded."""

    def test_renamed_library(self):
        ws = Workspace()
        try:
            baseline = ws.report("day1", "day2", as_of="2026-03-03")
            renames = {"CEL-": "KIND-", "DT-": "TYPE-", "PDA-A": "DET-", '"4A"': '"SVC-9"'}
            for path in [*ws.library.glob("*.json"), ws.clients / "C-1001.json"]:
                text = path.read_text(encoding="utf-8")
                for old, new in renames.items():
                    text = text.replace(old, new)
                path.write_text(text, encoding="utf-8")
            renamed = ws.report("day1", "day2", as_of="2026-03-03")
            undo = json.dumps(renamed)
            for old, new in renames.items():
                undo = undo.replace(new.strip('"'), old.strip('"'))
            self.assertEqual(re.sub(r"\s", "", undo), re.sub(r"\s", "", json.dumps(baseline)))
        finally:
            ws.close()


if __name__ == "__main__":
    unittest.main()
