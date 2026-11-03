
import json
import unittest
from pathlib import Path

from ges_intake.readiness import evaluate_readiness


ROOT = Path(__file__).resolve().parents[1]


class TestReadiness(unittest.TestCase):

    def setUp(self):
        with (ROOT / "library" / "cel.json").open(encoding="utf-8") as f:
            self.cel_config = json.load(f)

        with (ROOT / "library" / "services.json").open(encoding="utf-8") as f:
            self.service_config = json.load(f)

        with (ROOT / "clients" / "C-1001.json").open(encoding="utf-8") as f:
            self.client = json.load(f)

    def test_empty_documents(self):
        result = evaluate_readiness(
            documents=[],
            client=self.client,
            cel_config=self.cel_config,
            service_config=self.service_config,
            as_of="2026-03-01"
        )

        self.assertEqual(result["status"], "NOT_READY")
        self.assertEqual(len(result["pdas"]), 5)

        statuses = {
            pda["id"]: pda["status"]
            for pda in result["pdas"]
        }

        self.assertEqual(statuses["PDA-A01"], "MISSING")
        self.assertEqual(statuses["PDA-A02"], "MISSING")
        self.assertEqual(statuses["PDA-A03"], "MISSING")
        self.assertEqual(statuses["PDA-A04"], "MISSING")
        self.assertEqual(statuses["PDA-A05"], "MISSING")

    def test_quarantined_document_blocks_service(self):
        documents = [{
            "document_id": "DOC-QUARANTINED",
            "cel": "CEL-TAX-001",
            "status": "quarantined",
            "grade": "E5",
            "provenance": None,
            "fields": {}
        }]

        result = evaluate_readiness(
            documents=documents,
            client=self.client,
            cel_config=self.cel_config,
            service_config=self.service_config,
            as_of="2026-03-01"
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            result["security_holds"],
            ["document:DOC-QUARANTINED"]
        )


if __name__ == "__main__":
    unittest.main()
