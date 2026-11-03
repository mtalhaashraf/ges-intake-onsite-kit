import hashlib
import os
import json
from datetime import datetime, timezone
from pathlib import Path


def load_manifest(inbox_path: str | Path, client_id: str) -> dict:
    """
    Load and validate an inbox manifest.

    Fatal manifest errors raise ValueError.
    """
    manifest_path = Path(inbox_path) / "manifest.json"

    if not manifest_path.is_file():
        raise ValueError("Manifest is missing or is not a regular file")

    try:
        with manifest_path.open("r", encoding="utf-8-sig") as file:
            manifest = json.load(file)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        raise ValueError("Manifest is invalid or cannot be read") from None

    if not isinstance(manifest, dict):
        raise ValueError("Manifest must be a JSON object")

    entries = manifest.get("entries")

    if not isinstance(entries, list):
        raise ValueError("Manifest must contain an entries list")

    if manifest.get("client_id") != client_id:
        raise ValueError("Manifest client ID does not match requested client")

    return manifest


def parse_received_at(value: object) -> datetime | None:
    """
    Parse an ISO 8601 timestamp.

    Return an aware datetime when valid.
    Return None when invalid or missing a UTC offset.
    """
    if not isinstance(value, str) or not value:
        return None

    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None

    return timestamp.astimezone(timezone.utc)


def sort_entries(entries: list) -> list:
    """
    Valid timestamps first, sorted by UTC instant and file.
    Invalid timestamps afterward, sorted by file.
    """

    valid_entries = []
    invalid_entries = []

    for entry in entries:
        if not isinstance(entry, dict):
            invalid_entries.append(entry)
            continue

        timestamp = parse_received_at(entry.get("received_at"))

        if timestamp is None:
            invalid_entries.append(entry)
        else:
            valid_entries.append((timestamp, entry))

    valid_entries.sort(
        key=lambda item: (
            item[0],
            item[1].get("file") if isinstance(item[1].get("file"), str) else ""
        )
    )

    invalid_entries.sort(
        key=lambda entry: (
            entry.get("file") if isinstance(entry, dict)
            and isinstance(entry.get("file"), str) else ""
        )
    )

    return (
        [entry for _, entry in valid_entries]
        + invalid_entries
    )


def validate_file(
    inbox_path: str | Path,
    entry: dict,
    policy: dict
) -> dict:
    """Validate one manifest entry in the required check order."""

    # 1. Validate timestamp
    if parse_received_at(entry.get("received_at")) is None:
        return {"valid": False, "reason": "invalid_timestamp"}

    # 2. Validate channel
    if entry.get("channel") not in policy["channels"]:
        return {"valid": False, "reason": "invalid_channel"}

    # 3. Validate relative path and ensure it stays inside inbox
    file_value = entry.get("file")

    if (
        not isinstance(file_value, str)
        or not file_value
        or "\x00" in file_value
    ):
        return {"valid": False, "reason": "unsafe_path"}

    inbox = Path(inbox_path).resolve()

    try:
        relative_path = Path(file_value)

        if relative_path.is_absolute() or relative_path.drive:
            return {"valid": False, "reason": "unsafe_path"}

        candidate = inbox / relative_path
        resolved_path = candidate.resolve()
        resolved_path.relative_to(inbox)

    except (ValueError, OSError, RuntimeError):
        return {"valid": False, "reason": "unsafe_path"}

    # 4. Check existence
    if not os.path.lexists(candidate):
        return {"valid": False, "reason": "missing_file"}

    # 5. Reject symlinks
    if candidate.is_symlink():
        return {"valid": False, "reason": "symlink"}

    # 6. Require a regular file
    if not candidate.is_file():
        return {"valid": False, "reason": "unsafe_path"}

    file_path = candidate

    # 7. Validate extension
    allowed_extensions = [
        extension.lower()
        for extension in policy["allowed_extensions"]
    ]

    if file_path.suffix.lower() not in allowed_extensions:
        return {"valid": False, "reason": "unsupported_type"}

    # 8. Validate size and read no more than max_bytes + 1
    max_bytes = policy["max_bytes"]

    try:
        if file_path.stat().st_size > max_bytes:
            return {"valid": False, "reason": "too_large"}

        with file_path.open("rb") as file:
            raw_bytes = file.read(max_bytes + 1)

    except OSError:
        return {"valid": False, "reason": "missing_file"}

    if len(raw_bytes) > max_bytes:
        return {"valid": False, "reason": "too_large"}

    # 9. Reject empty files
    if not raw_bytes:
        return {"valid": False, "reason": "empty"}

    # 10. Validate UTF-8
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {"valid": False, "reason": "not_utf8"}

    return {
        "valid": True,
        "path": file_path,
        "raw_bytes": raw_bytes,
        "text": text
    }
   
def generate_document_identity(client_id: str, raw_bytes: bytes) -> dict:
    """
    Generate SHA-256 and document ID from the original file bytes.
    """
    sha256 = hashlib.sha256(raw_bytes).hexdigest()

    document_id = "DOC-" + hashlib.sha256(
        f"{client_id}:{sha256}".encode("utf-8")
    ).hexdigest()[:12]

    return {
        "sha256": sha256,
        "document_id": document_id
    }


def is_duplicate(document_id: str, seen_ids: set) -> bool:
    """
    Check whether a document ID has already been processed
    during the current run.

    The same set must be shared across all inboxes.
    """
    if document_id in seen_ids:
        return True

    seen_ids.add(document_id)
    return False
