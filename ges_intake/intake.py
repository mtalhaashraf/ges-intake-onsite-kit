import os
from pathlib import Path

from ges_intake.process import parse_timestamp


def check_entry(entry, inbox_dir, policy):
    """Run all 10 intake checks on a single manifest entry.

    Returns (None, raw_bytes) if all checks pass.
    Returns (reason_string, None) if any check fails.
    """
    inbox_path = Path(inbox_dir).resolve()
    max_bytes = policy["max_bytes"]
    allowed_ext = [e.lower() for e in policy["allowed_extensions"]]
    allowed_channels = policy["channels"]

    # --- Check 1: valid timestamp (ISO 8601 with UTC offset or Z) ---
    # If the timestamp is missing or has no timezone, reject.
    dt = parse_timestamp(entry.get("received_at"))
    if dt is None:
        return ("invalid_timestamp", None)

    # --- Check 2: channel must be in the allowed list ---
    channel = entry.get("channel", "")
    if channel not in allowed_channels:
        return ("invalid_channel", None)

    # --- Check 3: file path must be safe ---
    # Must be non-empty, relative, no NUL bytes, and resolve inside inbox.
    raw_file = entry.get("file", "")
    if not raw_file or "\x00" in raw_file:
        return ("unsafe_path", None)

    # Must be relative (not absolute)
    if os.path.isabs(raw_file):
        return ("unsafe_path", None)

    # Resolve and confirm it stays inside the inbox directory
    try:
        resolved = (inbox_path / raw_file).resolve()
    except (OSError, ValueError):
        return ("unsafe_path", None)

    # The resolved path must be INSIDE the inbox directory
    try:
        resolved.relative_to(inbox_path)
    except ValueError:
        return ("unsafe_path", None)

    # --- Check 4: file must exist ---
    if not resolved.exists():
        return ("missing_file", None)

    # --- Check 5: must not be a symlink ---
    # Check the un-resolved path to catch symlinks
    raw_path = inbox_path / raw_file
    if raw_path.is_symlink():
        return ("symlink", None)

    # --- Check 6: must be a regular file (not directory, device, etc.) ---
    if not resolved.is_file():
        return ("unsafe_path", None)

    # --- Check 7: extension must be in allowed list ---
    ext = resolved.suffix.lower()
    if ext not in allowed_ext:
        return ("unsupported_type", None)

    # --- Check 8: size must be ≤ max_bytes ---
    file_size = resolved.stat().st_size
    if file_size > max_bytes:
        return ("too_large", None)

    # --- Check 9: must not be empty ---
    if file_size == 0:
        return ("empty", None)

    # --- Check 10: must decode as UTF-8 ---
    # Read at most max_bytes + 1 bytes (never more)
    raw_bytes = resolved.read_bytes()[:max_bytes + 1]
    try:
        raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return ("not_utf8", None)

    return (None, raw_bytes)


def find_ignored_files(inbox_dir, entries):
    """Find top-level files in inbox not referenced by any entry (§4.1).

    These are reported in the 'ignored' list. manifest.json itself is excluded.
    """
    inbox_path = Path(inbox_dir)
    referenced = {e.get("file", "") for e in entries}
    referenced.add("manifest.json")

    ignored = []
    for item in inbox_path.iterdir():
        if item.is_file() and item.name not in referenced:
            ignored.append(item.name)

    return sorted(ignored)
