import hashlib
import os
import re
from dataclasses import dataclass

from vendor.advisory_stub import request_suggestion

from .attention import build_attention
from .govern import Doc, govern_document, normalize
from .intake import ignored_files, intake_file, processing_order, read_manifest
from .library import load_client, load_library
from .readiness import evaluate_readiness, mark_superseded_documents


@dataclass
class Batch:
    inbox: str
    manifest: dict
    ignored: list


def document_id(client_id, data):
    file_hash = hashlib.sha256(data).hexdigest()
    return "DOC-" + hashlib.sha256(f"{client_id}:{file_hash}".encode()).hexdigest()[:12]


def redact(text):
    return re.sub(r"[0-9]", "#", text)


def ask_advisory(text, outbox):
    try:
        answer = request_suggestion(redact(normalize(text)), outbox)
    except OSError:
        return {"error": "advisory service unavailable"}
    return {
        "doc_type": answer.get("doc_type"),
        "confidence": answer.get("confidence"),
        "rationale": answer.get("rationale"),
        "applied": False,
    }


def open_batch(inbox, client_id):
    inbox = os.path.realpath(inbox)
    manifest = read_manifest(inbox, client_id)
    return Batch(inbox, manifest, ignored_files(inbox, manifest["entries"]))


def ingest_batch(batch, docs, library, client, outbox):
    report = {
        "batch_id": batch.manifest.get("batch_id"),
        "client_id": batch.manifest["client_id"],
        "documents": [],
        "duplicates": [],
        "rejected": [],
        "ignored": batch.ignored,
    }
    new_docs = []
    for received_at, entry in processing_order(batch.manifest["entries"]):
        source = entry.get("file")
        reason, data = intake_file(entry, received_at, batch.inbox, library.policy)
        if reason:
            report["rejected"].append({"source": source, "reason": reason})
            continue
        doc_id = document_id(client.id, data)
        if doc_id in docs:
            report["duplicates"].append({"source": source, "document_id": doc_id})
            continue
        text = data.decode("utf-8")
        doc = govern_document(Doc(doc_id, source, received_at, entry["channel"]), text, library, client)
        if doc.doc_type is None:
            doc.advisory = ask_advisory(text, outbox)
        docs[doc_id] = doc
        new_docs.append(doc)
    mark_superseded_documents(list(docs.values()), library)
    report["documents"] = [doc.report() for doc in new_docs]
    return report


def run(args):
    library = load_library(args.library)
    client = load_client(args.clients, args.client, library)
    batches = [open_batch(inbox, client.id) for inbox in args.inbox]

    docs = {}
    batch_reports = [ingest_batch(batch, docs, library, client, args.outbox) for batch in batches]
    all_docs = sorted(docs.values(), key=lambda doc: doc.document_id)
    readiness = evaluate_readiness(all_docs, library, client, args.as_of)

    return {
        "client_id": client.id,
        "as_of": args.as_of.isoformat(),
        "batches": batch_reports,
        "documents": [{"document_id": doc.document_id, "status": doc.status} for doc in all_docs],
        "readiness": readiness,
        "attention": build_attention(readiness, all_docs, library),
    }
