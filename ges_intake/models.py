"""The governed document record."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ges_intake.fields import to_json

ACCEPTED = "accepted"
PENDING_REVIEW = "pending_review"
QUARANTINED = "quarantined"
SUPERSEDED = "superseded"


@dataclass
class Document:
    document_id: str
    sha256: str
    source: str  # the manifest `file`, scrubbed for display
    batch_id: str
    channel: str
    received_at: datetime
    doc_type: str | None
    cel: str | None
    provenance: str | None
    grade: str | None
    base_status: str  # accepted | pending_review | quarantined, as governed on arrival
    flags: frozenset[str]
    fields: dict[str, object]  # typed values; identifiers are already masked
    advisory: dict | None = None
    superseded_by: tuple[str, ...] = field(default=())

    @property
    def status(self) -> str:
        """Supersession adds history on top of the governed status; it never rewrites it."""
        return SUPERSEDED if self.superseded_by else self.base_status

    def report(self) -> dict:
        return {
            "source": self.source,
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "cel": self.cel,
            "provenance": self.provenance,
            "grade": self.grade,
            "status": self.status,
            "flags": sorted(self.flags),
            "fields": {name: to_json(value) for name, value in self.fields.items()},
            "advisory": self.advisory,
        }
