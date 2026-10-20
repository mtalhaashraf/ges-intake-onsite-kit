"""Identity, provenance, grade and status of a classified document (TASK §4.6)."""
from __future__ import annotations

from dataclasses import dataclass

from ges_intake.config import Client, DocType, Policy
from ges_intake.fields import Extraction
from ges_intake.models import ACCEPTED, PENDING_REVIEW, QUARANTINED
from ges_intake.text import name_tokens

IDENTITY_MISMATCH = "identity_mismatch"
NAME_MISMATCH = "name_mismatch"
UNKNOWN_ACCOUNT = "unknown_account"
CLAIMED_OFFICIAL = "claimed_official_source_unverified"


@dataclass(frozen=True)
class Governed:
    provenance: str | None
    grade: str
    status: str
    flags: frozenset[str]
    fields: dict[str, object]


def govern(doc_type: DocType, extraction: Extraction, channel: str, client: Client,
           policy: Policy, flags: set[str]) -> Governed:
    flags = set(flags) | extraction.flags
    fields = extraction.values

    if doc_type.ssn_field and doc_type.ssn_field in fields:
        if _ssn_differs(str(fields[doc_type.ssn_field]), extraction.ssn_full.get(doc_type.ssn_field), client):
            # Someone else's document. Keep nothing it says; it is a security incident.
            flags.add(IDENTITY_MISMATCH)
            return Governed(provenance=None, grade="E5", status=QUARANTINED, flags=frozenset(flags), fields={})

    if doc_type.name_field and doc_type.name_field in fields:
        if name_tokens(str(fields[doc_type.name_field])) != name_tokens(client.name):
            flags.add(NAME_MISMATCH)
    if doc_type.account_field and doc_type.account_field in fields:
        if fields[doc_type.account_field] not in client.accounts:
            flags.add(UNKNOWN_ACCOUNT)

    # Provenance comes from how the file reached us, never from what it says about itself.
    provenance = doc_type.provenance_by_channel.get(channel, doc_type.provenance_default)
    if doc_type.claims_official and channel not in doc_type.provenance_by_channel:
        flags.add(CLAIMED_OFFICIAL)

    if flags & policy.e4_flags:
        grade = "E4"
    elif provenance in ("P1", "P2"):
        grade = "E1"
    else:
        grade = "E3"  # P3; P4 (firm-prepared) is not independent evidence either, see NOTES.md
    status = PENDING_REVIEW if flags & policy.review_flags else ACCEPTED
    return Governed(provenance=provenance, grade=grade, status=status, flags=frozenset(flags), fields=fields)


def _ssn_differs(masked: str, full: str | None, client: Client) -> bool:
    if masked[-4:] != client.ssn_last4:
        return True
    return full is not None and client.ssn_full is not None and full != client.ssn_full
