
from datetime import datetime, timezone


def parse_received_at(value: str) -> datetime:
    """Convert an ISO timestamp into a comparable UTC datetime."""
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("received_at must include a UTC offset")

    return timestamp.astimezone(timezone.utc)


def supersede_documents(
    documents: list,
    document_types: list
) -> list:
    """
    Mark older documents as superseded.

    Each document must contain:
        document_id
        doc_type
        received_at
        fields
        status

    Returns new document dictionaries and preserves document history.
    """

    result = [dict(document) for document in documents]

    types_by_id = {
        document_type["id"]: document_type
        for document_type in document_types
    }

    # Compare every potential superseding document against every
    # other document.
    for newer in result:
        newer_type = types_by_id.get(newer.get("doc_type"))

        if not newer_type or "supersede" not in newer_type:
            continue

        rules = newer_type["supersede"]
        required_keys = rules.get("key", [])
        superseded_types = rules.get("supersedes", [])

        newer_fields = newer.get("fields", {})

        # A quarantined document cannot supersede another document.
        if newer.get("status") == "quarantined":
            continue

        # All configured key fields must exist in the newer document.
        if any(
            key not in newer_fields or newer_fields[key] is None
            for key in required_keys
        ):
            continue

        newer_time = parse_received_at(newer["received_at"])

        for older in result:
            if newer is older:
                continue

            if older.get("status") == "quarantined":
                continue

            if older.get("doc_type") not in superseded_types:
                continue

            older_fields = older.get("fields", {})

            # All key values must match.
            if any(
                key not in older_fields
                or older_fields[key] is None
                or newer_fields[key] != older_fields[key]
                for key in required_keys
            ):
                continue

            older_time = parse_received_at(older["received_at"])

            # Compare received timestamp first, document ID second.
            if (
                newer_time,
                newer["document_id"]
            ) > (
                older_time,
                older["document_id"]
            ):
                older["status"] = "superseded"

    return result
