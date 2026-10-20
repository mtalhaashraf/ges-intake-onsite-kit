"""Inbox manifests and processing order (TASK §4.1)."""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ges_intake.errors import InputError

MANIFEST = "manifest.json"

# ISO 8601 extended format with a mandatory offset. Parsed by hand: datetime.fromisoformat only
# accepts "Z" from Python 3.11, and is laxer than the spec in other ways.
_TIMESTAMP = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:[.,]([0-9]+))?)?"
    r"(?:(Z)|([+-])([0-9]{2})(?::?([0-9]{2}))?)"
)


@dataclass(frozen=True)
class Entry:
    file: str
    channel: object
    received_at: datetime | None  # None when the timestamp is invalid


@dataclass(frozen=True)
class Batch:
    inbox: Path
    client_id: str
    batch_id: str
    entries: tuple[Entry, ...]  # processing order


def parse_timestamp(value: object) -> datetime | None:
    """An aware UTC datetime, or None unless `value` is ISO 8601 with `Z` or a UTC offset."""
    if not isinstance(value, str) or not (m := _TIMESTAMP.fullmatch(value)):
        return None
    year, month, day, hour, minute, second, fraction, zulu, sign, off_h, off_m = m.groups()
    try:
        offset = timedelta(0)
        if not zulu:
            hours, minutes = int(off_h), int(off_m or 0)
            if hours > 23 or minutes > 59:
                return None
            offset = timedelta(hours=hours, minutes=minutes) * (-1 if sign == "-" else 1)
        micro = int((fraction or "0")[:6].ljust(6, "0"))
        local = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second or 0), micro,
                         tzinfo=timezone(offset))
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def load_batch(inbox: Path, client_id: str) -> Batch:
    """Read and check one manifest. Any problem is an InputError: the whole run stops unchanged."""
    path = inbox / MANIFEST
    try:
        info = os.lstat(path)
    except OSError:
        raise InputError(f"{inbox}: missing {MANIFEST}") from None
    if not stat.S_ISREG(info.st_mode):
        raise InputError(f"{inbox}: {MANIFEST} is not a regular file")
    try:
        manifest = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError):
        raise InputError(f"{inbox}: {MANIFEST} is not valid JSON") from None
    if not isinstance(manifest, dict):
        raise InputError(f"{inbox}: {MANIFEST} must be a JSON object")
    if manifest.get("client_id") != client_id:
        raise InputError(f"{inbox}: manifest client_id does not match --client")
    batch_id = manifest.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise InputError(f"{inbox}: manifest has no batch_id")
    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise InputError(f"{inbox}: manifest has no entries list")

    entries = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or not isinstance(raw.get("file"), str):
            raise InputError(f"{inbox}: manifest entry {index} has no file name")
        entries.append((Entry(file=raw["file"], channel=raw.get("channel"),
                              received_at=parse_timestamp(raw.get("received_at"))),
                        json.dumps(raw, sort_keys=True, ensure_ascii=True)))
    return Batch(inbox=inbox, client_id=client_id, batch_id=batch_id, entries=_processing_order(entries))


def _processing_order(entries: list[tuple[Entry, str]]) -> tuple[Entry, ...]:
    """Valid timestamps by (UTC instant, file), then invalid ones by file. Manifest order means nothing,
    so exact ties fall back to the entry's canonical JSON rather than its position."""
    timed = sorted((e for e in entries if e[0].received_at), key=lambda e: (e[0].received_at, e[0].file, e[1]))
    untimed = sorted((e for e in entries if not e[0].received_at), key=lambda e: (e[0].file, e[1]))
    return tuple(entry for entry, _ in timed + untimed)
