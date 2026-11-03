"""Entry point: python -m ges_intake --library DIR --clients DIR run --client ID --inbox DIR [--inbox DIR ...] --as-of DATE --outbox FILE

The argument parsing and output format below are provided so you can spend your time on the
engine. Everything else (modules, classes, how the stages fit together) is your design.
Exit codes: 0 success, 2 bad input / bad library / unknown client.
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from ges_intake.intake import (
    load_manifest,
    sort_entries,
    validate_file,
    generate_document_identity,
    is_duplicate,
)
from ges_intake.classification import classify_document
from ges_intake.extraction import extract_fields
from ges_intake.governance import evaluate_governance
from ges_intake.supersession import supersede_documents
from ges_intake.readiness import evaluate_readiness
from vendor.advisory_stub import request_suggestion


CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


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


def read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def validate_library(policy, document_types, cel_config, service_config):
    """Validate references between the supplied library configuration files."""

    if not isinstance(policy, dict):
        raise ValueError("Invalid intake policy")

    if not isinstance(document_types, list):
        raise ValueError("Invalid document types configuration")

    if not isinstance(cel_config.get("cel"), list):
        raise ValueError("Invalid CEL configuration")

    if not isinstance(service_config.get("services"), list):
        raise ValueError("Invalid services configuration")

    type_ids = [item.get("id") for item in document_types]
    cel_ids = [item.get("id") for item in cel_config["cel"]]
    service_ids = [item.get("id") for item in service_config["services"]]

    if len(type_ids) != len(set(type_ids)):
        raise ValueError("Duplicate document type IDs")

    if len(cel_ids) != len(set(cel_ids)):
        raise ValueError("Duplicate CEL IDs")

    if len(service_ids) != len(set(service_ids)):
        raise ValueError("Duplicate service IDs")

    known_types = set(type_ids)
    known_cels = set(cel_ids)
    known_services = set(service_ids)

    for doc_type in document_types:
        if doc_type.get("cel") not in known_cels:
            raise ValueError("Document type references an unknown CEL")

        provenance = doc_type.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ValueError("Invalid document provenance configuration")

        for rule in doc_type.get("supersede", {}).get("supersedes", []):
            if rule not in known_types:
                raise ValueError("Unknown supersession document type")

    for service in service_config["services"]:
        pda_ids = [pda.get("id") for pda in service.get("pdas", [])]

        if len(pda_ids) != len(set(pda_ids)):
            raise ValueError("Duplicate PDA IDs")

        known_pdas = set(pda_ids)
        graph = {}

        for pda in service.get("pdas", []):
            dependencies = pda.get("depends_on", [])

            if any(dep not in known_pdas for dep in dependencies):
                raise ValueError("PDA references an unknown dependency")

            graph[pda["id"]] = dependencies

            for pathway in pda.get("pathways", []):
                for requirement in pathway.get("requirements", []):
                    if requirement.get("cel") not in known_cels:
                        raise ValueError("PDA references an unknown CEL")

        # Detect dependency cycles.
        visiting = set()
        visited = set()

        def visit(pda_id):
            if pda_id in visiting:
                raise ValueError("PDA dependency cycle detected")

            if pda_id in visited:
                return

            visiting.add(pda_id)

            for dependency in graph[pda_id]:
                visit(dependency)

            visiting.remove(pda_id)
            visited.add(pda_id)

        for pda_id in graph:
            visit(pda_id)

    return True


def redact_advisory_text(text, document_types):
    """Redact sensitive fields before text leaves the intake process."""

    redacted = text

    sensitive_names = {
        "ssn",
        "employee_ssn",
        "recipient_tin",
        "payer_tin",
        "employer_ein",
        "ein",
        "account",
        "license_no",
        "passport_no",
    }

    # Use the configured extraction patterns wherever possible.
    for doc_type in document_types:
        fields = doc_type.get("fields", {})

        for field_name, specification in fields.items():
            if field_name.lower() not in sensitive_names:
                continue

            if isinstance(specification, str):
                pattern = specification
            elif isinstance(specification, dict):
                pattern = specification.get("pattern")
            else:
                pattern = None

            if not pattern:
                continue

            try:
                redacted = re.sub(
                    pattern,
                    "[REDACTED]",
                    redacted,
                    flags=re.MULTILINE | re.IGNORECASE,
                )
            except re.error as exc:
                raise ValueError(
                    "Invalid sensitive-field redaction pattern"
                ) from exc

    # Additional common identifier formats.
    redacted = re.sub(
        r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
        "[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?<!\d)\d{2}-\d{7}(?!\d)",
        "[REDACTED]",
        redacted,
    )

    return redacted


def get_ignored_files(inbox_path, entries):
    """Return unreferenced top-level files without reading their contents."""

    inbox = Path(inbox_path)

    referenced = {
        entry.get("file")
        for entry in entries
        if isinstance(entry.get("file"), str)
    }

    ignored = []

    for path in inbox.iterdir():
        if path.name == "manifest.json":
            continue

        if path.is_file() and path.name not in referenced:
            ignored.append(path.name)

    return sorted(ignored)


def make_public_document(document):
    """Build a safe report representation without extracted identifiers."""

    return {
        "document_id": document["document_id"],
        "doc_type": document.get("doc_type"),
        "cel": document.get("cel"),
        "provenance": document.get("provenance"),
        "grade": document.get("grade"),
        "status": document.get("status"),
        "flags": sorted(set(document.get("flags", []))),
        **(
            {"suggestion": document["suggestion"]}
            if document.get("suggestion") is not None
            else {}
        ),
    }


def run(args) -> dict:
    # Never use an unchecked client ID as a path fragment.
    if not CLIENT_ID_PATTERN.fullmatch(args.client):
        raise ValueError("Malformed client ID")

    library_path = Path(args.library)
    clients_path = Path(args.clients)

    client_file = clients_path / f"{args.client}.json"

    if not client_file.is_file():
        raise ValueError("Unknown client ID")

    # Load and validate configuration before processing any inbox.
    policy = read_json(library_path / "intake_policy.json")
    types_config = read_json(library_path / "document_types.json")
    cel_config = read_json(library_path / "cel.json")
    service_config = read_json(library_path / "services.json")
    client = read_json(client_file)

    if client.get("client_id") != args.client:
        raise ValueError("Client profile ID mismatch")

    validate_library(
        policy,
        types_config.get("document_types"),
        cel_config,
        service_config,
    )

    document_types = types_config["document_types"]

    # Validate every manifest before processing any document.
    manifests = []

    for inbox_value in args.inbox:
        inbox_path = Path(inbox_value)

        if not inbox_path.is_dir():
            raise ValueError("Inbox directory does not exist")

        manifest = load_manifest(inbox_path, args.client)
        entries = sort_entries(manifest["entries"])

        manifests.append({
            "path": inbox_path,
            "manifest": manifest,
            "entries": entries,
            "ignored": get_ignored_files(inbox_path, manifest["entries"]),
        })

    all_documents = []
    seen_ids = set()
    batches = []

    for batch_data in manifests:
        inbox_path = batch_data["path"]
        manifest = batch_data["manifest"]
        entries = batch_data["entries"]

        batch_documents = []
        duplicates = []
        rejected = []

        new_document_ids = []

        for entry in entries:
            source = entry.get("original_name")

            validation = validate_file(inbox_path, entry, policy)

            if not validation["valid"]:
                rejected.append({
                    "source": source,
                    "reason": validation["reason"],
                })
                continue

            identity = generate_document_identity(
                args.client,
                validation["raw_bytes"],
            )

            document_id = identity["document_id"]

            if is_duplicate(document_id, seen_ids):
                duplicates.append({
                    "source": source,
                    "document_id": document_id,
                })
                continue

            seen_ids.add(document_id)

            classification = classify_document(
                validation["text"],
                document_types,
                policy["suspicious_instruction_patterns"],
            )

            document_type = classification["doc_type"]

            combined_flags = list(classification["flags"])

            document = {
                "document_id": document_id,
                "sha256": identity["sha256"],
                "source": source,
                "received_at": entry["received_at"],
                "doc_type": None,
                "cel": None,
                "fields": {},
                "provenance": None,
                "grade": None,
                "status": "pending_review",
                "flags": combined_flags,
                "security_incident": None,
                "suggestion": None,
            }

            if document_type is not None:
                extraction = extract_fields(
                    validation["text"],
                    document_type,
                )

                combined_flags.extend(extraction["flags"])
                combined_flags = list(dict.fromkeys(combined_flags))

                governance = evaluate_governance(
                    validation["text"],
                    document_type,
                    extraction["fields"],
                    combined_flags,
                    client,
                    entry["channel"],
                    policy,
                )

                document.update({
                    "doc_type": document_type["id"],
                    "cel": document_type["cel"],
                    "fields": governance["fields"],
                    "provenance": governance["provenance"],
                    "grade": governance["grade"],
                    "status": governance["status"],
                    "flags": sorted(set(governance["flags"])),
                    "security_incident": governance.get(
                        "security_incident"
                    ),
                })

            else:
                # No classification means no extraction or governance guess.
                # The advisory model receives only redacted text.
                safe_text = redact_advisory_text(
                    validation["text"],
                    document_types,
                )

                document["suggestion"] = request_suggestion(
                    safe_text,
                    str(args.outbox),
                )

            all_documents.append(document)
            new_document_ids.append(document_id)

        # Apply supersession after each batch so that this batch's report
        # captures statuses at the end of this batch.
        all_documents = supersede_documents(
            all_documents,
            document_types,
        )

        documents_by_id = {
            document["document_id"]: document
            for document in all_documents
        }

        for document_id in new_document_ids:
            document = documents_by_id[document_id]

            batch_documents.append({
                "source": document["source"],
                "document_id": document["document_id"],
                "doc_type": document["doc_type"],
                "cel": document["cel"],
                "provenance": document["provenance"],
                "grade": document["grade"],
                "status": document["status"],
                "flags": sorted(set(document["flags"])),
            })

        batches.append({
            "batch_id": manifest["batch_id"],
            "client_id": manifest["client_id"],
            "documents": batch_documents,
            "duplicates": duplicates,
            "rejected": rejected,
            "ignored": batch_data["ignored"],
        })

    # Final statuses after every batch and all supersession rules.
    public_documents = [
        make_public_document(document)
        for document in sorted(
            all_documents,
            key=lambda item: item["document_id"],
        )
    ]

    readiness = evaluate_readiness(
        all_documents,
        client,
        cel_config,
        service_config,
        args.as_of,
    )

    # Add metadata required by TASK.md §3.
    readiness["client_id"] = args.client
    readiness["as_of"] = args.as_of.isoformat()
    readiness["library_version"] = cel_config["library_version"]

    # The output contract uses "service", not service_id/service_name.
    readiness["service"] = readiness.pop("service_id")

    # Remove extra service metadata not required by the output contract.
    readiness.pop("service_name", None)

    # Include dependencies in each PDA result.
    service = next(
        item for item in service_config["services"]
        if item["id"] == client["engagement"]["service"]
    )

    pda_config_by_id = {
        pda["id"]: pda
        for pda in service["pdas"]
    }

    for pda_result in readiness["pdas"]:
        pda_config = pda_config_by_id[pda_result["id"]]
        pda_result["depends_on"] = pda_config.get("depends_on", [])

    return {
        "client_id": args.client,
        "as_of": args.as_of.isoformat(),
        "batches": batches,
        "documents": public_documents,
        "readiness": readiness,
    }


def main(argv=None) -> int:
    try:
        args = parse_args(argv)
        report = run(args)

    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 2

    sys.stdout.write(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
