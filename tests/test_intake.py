import json
import os
import unittest

from tests.helpers import Workspace


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.day1 = self.ws.inbox / "day1"

    def tearDown(self):
        self.ws.close()

    def rejected(self, report):
        return {r["source"]: r["reason"] for r in report["batches"][0]["rejected"]}

    def add_entries(self, *entries):
        def change(manifest):
            manifest["entries"].extend(
                {"file": f, "original_name": f, "channel": "portal_upload", "received_at": "2026-02-22T00:00:00Z"}
                for f in entries)
        self.ws.edit_json(self.day1 / "manifest.json", change)

    def test_day1_rejections_in_processing_order(self):
        report = self.ws.report()
        self.assertEqual(report["batches"][0]["rejected"], [
            {"source": "../outside_secret.txt", "reason": "unsafe_path"},
            {"source": "empty.txt", "reason": "empty"},
            {"source": "huge.txt", "reason": "too_large"},
            {"source": "latin1.txt", "reason": "not_utf8"},
            {"source": "scan.jpg", "reason": "unsupported_type"},
            {"source": "ghost.txt", "reason": "missing_file"},
            {"source": "stmt_9930_2025_03.txt", "reason": "invalid_channel"},
            {"source": "dl_scan.txt", "reason": "invalid_timestamp"},
        ])
        self.assertEqual(report["batches"][0]["ignored"], ["notes_unlisted.txt"])

    def test_symlinks_and_escapes(self):
        os.symlink("../outside_secret.txt", self.day1 / "link.txt")
        os.symlink("/nonexistent/target.txt", self.day1 / "dangling.txt")
        os.mkdir(self.ws.path / "elsewhere")
        (self.ws.path / "elsewhere" / "x.txt").write_text("Account Number: 0004 5512 4821\n")
        os.symlink(self.ws.path / "elsewhere", self.day1 / "subdir")
        os.mkdir(self.day1 / "realdir")
        os.link(self.ws.inbox / "outside_secret.txt", self.day1 / "hardlink.txt")
        self.add_entries("link.txt", "dangling.txt", "subdir/x.txt", "realdir", "/etc/hosts", "a/../../x.txt",
                         "", "bad\x00.txt", "..\\outside_secret.txt", "hardlink.txt", "NOTES_UNLISTED.TXT")
        report = self.ws.report()
        reasons = self.rejected(report)
        self.assertEqual(reasons["link.txt"], "symlink")
        self.assertEqual(reasons["dangling.txt"], "symlink")
        self.assertEqual(reasons["subdir/x.txt"], "unsafe_path")
        self.assertEqual(reasons["realdir"], "unsafe_path")
        self.assertEqual(reasons["/etc/hosts"], "unsafe_path")
        self.assertEqual(reasons["a/../../x.txt"], "unsafe_path")
        self.assertEqual(reasons[""], "unsafe_path")
        self.assertEqual(reasons["bad\x00.txt"], "unsafe_path")
        self.assertEqual(reasons["..\\outside_secret.txt"], "unsafe_path")
        self.assertEqual(reasons["hardlink.txt"], "unsafe_path")
        self.assertEqual(reasons["NOTES_UNLISTED.TXT"], "missing_file")  # exact spelling only
        self.assertNotIn("0004 5512", json.dumps(report))

    def test_bom_crlf_statement_is_read(self):
        report = self.ws.report()
        doc = next(d for d in report["batches"][0]["documents"] if d["source"] == "stmt_4821_2025_06.txt")
        self.assertEqual(doc["status"], "accepted")
        self.assertEqual(doc["fields"]["period_start"], "2025-06-01")
        self.assertEqual(doc["fields"]["ending_balance"], "7250.25")

    def test_manifest_order_means_nothing(self):
        before = self.ws.run().stdout
        self.ws.edit_json(self.day1 / "manifest.json", lambda m: m["entries"].reverse())
        self.assertEqual(self.ws.run().stdout, before)


if __name__ == "__main__":
    unittest.main()
