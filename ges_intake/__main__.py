"""Entry point: python -m ges_intake --library DIR --clients DIR run --client ID --inbox DIR [--inbox DIR ...] --as-of DATE --outbox FILE

The argument parsing and output format below are provided so you can spend your time on the
engine. Everything else (modules, classes, how the stages fit together) is your design.
Exit codes: 0 success, 2 bad input / bad library / unknown client.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="ges_intake")
    p.add_argument("--library", required=True)
    p.add_argument("--clients", required=True)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--client", required=True)
    r.add_argument("--inbox", required=True, action="append", help="repeatable; processed in the given order")
    r.add_argument("--as-of", required=True, type=date.fromisoformat)
    r.add_argument("--outbox", required=True, help="file the advisory stub appends to")
    return p.parse_args(argv)


def load_manifest(inbox_dir, expected_client_id):
    """Load and validate the basic structure of an inbox manifest."""
    manifest_path = Path(inbox_dir) / "manifest.json"

    try:
        with manifest_path.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load manifest: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")

    if not isinstance(manifest.get("entries"), list):
        raise ValueError("Manifest must contain an entries list")

    if manifest.get("client_id") != expected_client_id:
        raise ValueError("Manifest client_id does not match requested client")

    return manifest


def load_library(library_dir):
    """Load all library JSON files."""
    ld = Path(library_dir)
    def _load(name):
        with (ld / name).open("r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "cel": _load("cel.json"),
        "document_types": _load("document_types.json"),
        "services": _load("services.json"),
        "intake_policy": _load("intake_policy.json"),
    }


def load_client(clients_dir, client_id):
    """Load and validate a client profile."""
    import re
    if not re.fullmatch(r"C-\d+", client_id):
        raise ValueError(f"Invalid client ID format: {client_id}")
    client_path = Path(clients_dir) / f"{client_id}.json"
    try:
        with client_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot load client {client_id}: {exc}") from exc


def run(args) -> dict:
    """Full engine: intake, classify, govern, supersede, readiness."""
    from ges_intake.process import sort_entries
    from ges_intake.intake import check_entry, find_ignored_files
    from ges_intake.identity import compute_sha256, compute_document_id, is_duplicate
    from ges_intake.classify import normalise_text, phrase_view, classify_document, check_suspicious_patterns
    from ges_intake.extract import extract_fields
    from ges_intake.govern import check_identity, determine_provenance, determine_grade, determine_status
    from ges_intake.readiness import evaluate_readiness
    from ges_intake.supersede import apply_supersession
    from ges_intake.advisory import get_advisory_suggestion
    from ges_intake.validate import validate_library

    library = load_library(args.library)
    validate_library(library)

    client = load_client(args.clients, args.client)
    policy = library["intake_policy"]
    doc_types = library["document_types"]["document_types"]
    doc_types_lookup = {dt["id"]: dt for dt in doc_types}
    suspicious_patterns = policy.get("suspicious_instruction_patterns", [])
    review_flags = set(policy.get("review_flags", []))
    e4_flags = set(policy.get("e4_flags", []))

    batches_out = []
    all_documents = []
    seen_ids = set()

    for inbox_dir in args.inbox:
        manifest = load_manifest(inbox_dir, args.client)
        ordered_entries = sort_entries(manifest.get("entries", []))

        batch_documents = []
        duplicates = []
        rejected = []
        ignored = find_ignored_files(inbox_dir, manifest.get("entries", []))

        for entry in ordered_entries:
            reason, raw_bytes = check_entry(entry, inbox_dir, policy)
            if reason:
                rejected.append({
                    "source": entry.get("file", ""),
                    "reason": reason,
                })
                continue

            sha256 = compute_sha256(raw_bytes)
            doc_id = compute_document_id(args.client, sha256)

            if is_duplicate(doc_id, seen_ids):
                duplicates.append({
                    "source": entry.get("file", ""),
                    "document_id": doc_id,
                })
                continue

            norm_text = normalise_text(raw_bytes)
            phrase = phrase_view(norm_text)

            matched_type, class_flags = classify_document(phrase, doc_types)
            suspicious_flags = check_suspicious_patterns(phrase, suspicious_patterns)
            flags = sorted(set(class_flags + suspicious_flags))

            advisory_suggestion = None

            if matched_type:
                doc_type_id = matched_type["id"]
                cel_id = matched_type["cel"]
                extracted, extract_flags = extract_fields(norm_text, matched_type)
                flags = sorted(set(flags + extract_flags))

                id_flags, quarantined, extracted = check_identity(extracted, matched_type, client)
                flags = sorted(set(flags + id_flags))

                channel = entry.get("channel", "")
                provenance, prov_flags = determine_provenance(matched_type, channel)
                flags = sorted(set(flags + prov_flags))

                if quarantined:
                    provenance = None

                grade = determine_grade(provenance, flags, e4_flags)
                status = determine_status(grade, flags, review_flags)
            else:
                doc_type_id = None
                cel_id = None
                extracted = {}
                provenance = None
                grade = None
                status = "pending_review"
                # Advisory classifier for unclassified docs (Tier 3)
                advisory_suggestion = get_advisory_suggestion(norm_text, args.outbox)

            doc_record = {
                "source": entry.get("file", ""),
                "document_id": doc_id,
                "doc_type": doc_type_id,
                "cel": cel_id,
                "provenance": provenance,
                "grade": grade,
                "status": status,
                "flags": flags,
                "_matched_type": matched_type,
                "_entry": entry,
                "_fields": extracted,
            }
            if advisory_suggestion:
                doc_record["advisory_suggestion"] = advisory_suggestion
            batch_documents.append(doc_record)
            all_documents.append(doc_record)

        # Apply supersession after each batch so cross-batch supersession works
        apply_supersession(all_documents, doc_types_lookup)

        batches_out.append({
            "batch_id": manifest.get("batch_id", ""),
            "client_id": manifest.get("client_id", ""),
            "documents": [{k: v for k, v in d.items() if not k.startswith("_")}
                          for d in batch_documents],
            "duplicates": duplicates,
            "rejected": rejected,
            "ignored": ignored,
        })

    # Top-level documents: every document with FINAL status, sorted by document_id
    top_documents = sorted(
        [{"document_id": d["document_id"], "status": d["status"]} for d in all_documents],
        key=lambda x: x["document_id"],
    )

    cel_lookup = {c["id"]: c for c in library["cel"]["cel"]}

    service_id = client["engagement"]["service"]
    service = None
    for s in library["services"]["services"]:
        if s["id"] == service_id:
            service = s
            break
    if not service:
        raise ValueError(f"Unknown service: {service_id}")

    readiness = evaluate_readiness(all_documents, client, service, cel_lookup, args.as_of)
    readiness["library_version"] = library["cel"].get("library_version", "")

    from ges_intake.attention import compute_attention
    attention = compute_attention(readiness, all_documents, cel_lookup, service)

    return {
        "client_id": args.client,
        "as_of": args.as_of.isoformat(),
        "batches": batches_out,
        "documents": top_documents,
        "readiness": readiness,
        "attention": attention,
    }


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        report = run(args)
    except ValueError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2

    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
