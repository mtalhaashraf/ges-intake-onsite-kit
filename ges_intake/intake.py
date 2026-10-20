"""Intake checks (TASK §4.2): decide whether a manifest entry may be read, and read it safely.

The entry's `file` is untrusted. Nothing outside the inbox is opened, a symlink is never
followed, and no file is read beyond `max_bytes + 1` bytes.
"""
from __future__ import annotations

import os
import posixpath
import stat
from dataclasses import dataclass

from ges_intake.config import Policy
from ges_intake.manifest import MANIFEST, Entry

INVALID_TIMESTAMP = "invalid_timestamp"
INVALID_CHANNEL = "invalid_channel"
UNSAFE_PATH = "unsafe_path"
MISSING_FILE = "missing_file"
SYMLINK = "symlink"
UNSUPPORTED_TYPE = "unsupported_type"
TOO_LARGE = "too_large"
EMPTY = "empty"
NOT_UTF8 = "not_utf8"
UNREADABLE = "unreadable"  # an OS error the table does not cover (e.g. permission denied)

_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)


@dataclass(frozen=True)
class Received:
    data: bytes
    text: str


@dataclass(frozen=True)
class Rejected:
    reason: str


def check_entry(entry: Entry, inbox_real: str, policy: Policy) -> Received | Rejected:
    """Apply the checks in table order; the first failure is the reason."""
    if entry.received_at is None:
        return Rejected(INVALID_TIMESTAMP)
    if not isinstance(entry.channel, str) or entry.channel not in policy.channels:
        return Rejected(INVALID_CHANNEL)
    parts = safe_parts(entry.file)
    if parts is None:
        return Rejected(UNSAFE_PATH)
    path = os.path.join(inbox_real, *parts)
    parent = os.path.realpath(os.path.dirname(path))
    if parent != inbox_real and not parent.startswith(inbox_real + os.sep):
        return Rejected(UNSAFE_PATH)  # a directory on the way is a symlink out of the inbox
    if not _exists_exactly(inbox_real, parts):
        return Rejected(MISSING_FILE)
    try:
        info = os.lstat(path)
    except OSError:
        return Rejected(MISSING_FILE)
    if stat.S_ISLNK(info.st_mode):
        return Rejected(SYMLINK)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        # A hard link may be the same inode as a file outside the inbox.
        return Rejected(UNSAFE_PATH)
    if os.path.splitext(parts[-1])[1].lower() not in policy.allowed_extensions:
        return Rejected(UNSUPPORTED_TYPE)
    if info.st_size > policy.max_bytes:
        return Rejected(TOO_LARGE)
    if info.st_size == 0:
        return Rejected(EMPTY)
    return _read(path, info, policy.max_bytes)


def safe_parts(file: str) -> tuple[str, ...] | None:
    """Lexically safe path components inside the inbox, or None.

    Backslashes are refused outright: on Windows they are separators, so the same manifest
    would mean something different there.
    """
    if not file or "\x00" in file or "\\" in file or file.startswith("/"):
        return None
    normal = posixpath.normpath(file)
    if normal in (".", "..") or normal.startswith(("../", "/")):
        return None
    return tuple(normal.split("/"))


def _exists_exactly(root: str, parts: tuple[str, ...]) -> bool:
    """Every component exists with exactly this spelling. On a case-insensitive file system a
    plain exists() would accept "W2.TXT" for "w2.txt" and the run would differ between machines."""
    current = root
    for part in parts:
        try:
            if part not in os.listdir(current):
                return False
        except OSError:
            return False
        current = os.path.join(current, part)
    return True


def _read(path: str, expected: os.stat_result, max_bytes: int) -> Received | Rejected:
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except OSError:
        return Rejected(UNREADABLE)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino) or not stat.S_ISREG(opened.st_mode):
            return Rejected(UNSAFE_PATH)  # swapped between the checks and the open
        chunks, remaining = [], max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 16))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError:
        return Rejected(UNREADABLE)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        return Rejected(TOO_LARGE)
    if not data:
        return Rejected(EMPTY)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return Rejected(NOT_UTF8)
    return Received(data=data, text=text)


def ignored_files(inbox_real: str, entries: tuple[Entry, ...]) -> list[str]:
    """Top-level names no entry refers to. They are listed, never opened."""
    referenced = {entry.file for entry in entries}
    for entry in entries:
        parts = safe_parts(entry.file)
        if parts and len(parts) == 1:
            referenced.add(parts[0])
    names = []
    with os.scandir(inbox_real) as listing:
        for item in listing:
            if item.name == MANIFEST or item.name in referenced or item.is_dir(follow_symlinks=False):
                continue
            names.append(item.name)
    return names
