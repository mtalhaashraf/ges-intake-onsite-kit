"""The pipeline: intake -> identity -> classify -> extract & govern -> supersede -> readiness -> attention.

Every input (library, client, every manifest) is validated before anything is processed, so an
exit-2 condition anywhere leaves everything unchanged, including the advisory outbox.
"""
from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

from ges_intake import advisory, attention, readiness, supersede
from ges_intake.classify import SUSPICIOUS, classify, has_suspicious_instructions
from ges_intake.config import Client, Library, load_client, load_library
from ges_intake.fields import extract
from ges_intake.govern import govern
from ges_intake.intake import Rejected, check_entry, ignored_files
from ges_intake.manifest import Batch, Entry, load_batch
from ges_intake.models import PENDING_REVIEW, Document
from ges_intake.text import normalise, phrase_view, scrub_identifiers


def run(library_dir: str, clients_dir: str, client_id: str, inboxes: list[str], as_of: date, outbox: str) -> dict:
    library = load_library(Path(library_dir))
    client = load_client(Path(clients_dir), client_id, library)
    batches = [load_batch(Path(inbox), client.id) for inbox in inboxes]

    store: dict[str, Document] = {}
    batch_reports = [_ingest(batch, library, client, store, outbox) for batch in batches]
    documents = list(store.values())
    result = readiness.evaluate(library, client, documents, as_of)
    return {
        "client_id": client.id,
        "as_of": as_of.isoformat(),
        "batches": batch_reports,
        "documents": [{"document_id": d.document_id, "status": d.status}
                      for d in sorted(documents, key=lambda d: d.document_id)],
        "readiness": readiness.report(result, client, library, as_of),
        "attention": attention.build(library, client, documents, result),
    }


def document_id(client_id: str, data: bytes) -> tuple[str, str]:
    sha = hashlib.sha256(data).hexdigest()
    return sha, "DOC-" + hashlib.sha256(f"{client_id}:{sha}".encode()).hexdigest()[:12]


def _ingest(batch: Batch, library: Library, client: Client, store: dict[str, Document], outbox: str) -> dict:
    inbox_real = os.path.realpath(batch.inbox)
    new, duplicates, rejected = [], [], []
    for entry in batch.entries:
        source = scrub_identifiers(entry.file)
        outcome = check_entry(entry, inbox_real, library.policy)
        if isinstance(outcome, Rejected):
            rejected.append({"source": source, "reason": outcome.reason})
            continue
        sha, doc_id = document_id(client.id, outcome.data)
        if doc_id in store:
            duplicates.append({"source": source, "document_id": doc_id})
            continue
        doc = _govern(entry, batch, source, sha, doc_id, outcome.text, library, client, outbox)
        store[doc_id] = doc
        new.append(doc)
    supersede.apply(store.values(), library)
    return {
        "batch_id": batch.batch_id,
        "client_id": batch.client_id,
        "documents": [doc.report() for doc in new],  # status as of the end of this batch
        "duplicates": duplicates,
        "rejected": rejected,
        "ignored": sorted(scrub_identifiers(name) for name in ignored_files(inbox_real, batch.entries)),
    }


def _govern(entry: Entry, batch: Batch, source: str, sha: str, doc_id: str, raw_text: str,
            library: Library, client: Client, outbox: str) -> Document:
    text = normalise(raw_text)
    view = phrase_view(text)
    flags = {SUSPICIOUS} if has_suspicious_instructions(view, library.policy) else set()
    common = dict(document_id=doc_id, sha256=sha, source=source, batch_id=batch.batch_id,
                  channel=entry.channel, received_at=entry.received_at)

    doc_type, unclassified = classify(view, library.doc_types)
    if doc_type is None:
        flags.add(unclassified)
        return Document(**common, doc_type=None, cel=None, provenance=None, grade=None,
                        base_status=PENDING_REVIEW, flags=frozenset(flags), fields={},
                        advisory=advisory.request(text, client, outbox))

    governed = govern(doc_type, extract(doc_type, text), entry.channel, client, library.policy, flags)
    return Document(**common, doc_type=doc_type.id, cel=doc_type.cel, provenance=governed.provenance,
                    grade=governed.grade, base_status=governed.status, flags=governed.flags,
                    fields=governed.fields)
