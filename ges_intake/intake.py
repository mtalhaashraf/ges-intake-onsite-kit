import json
import os
import stat
from datetime import datetime, timezone

from .library import EngineError


def read_manifest(inbox, client_id):
    try:
        with open(os.path.join(inbox, "manifest.json"), encoding="utf-8") as file:
            manifest = json.load(file)
    except (OSError, ValueError) as error:
        raise EngineError(f"missing or malformed manifest in {inbox}: {error}") from None
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise EngineError(f"manifest in {inbox} has no valid entries list")
    if manifest.get("client_id") != client_id:
        raise EngineError(f"manifest in {inbox} belongs to a different client")
    return manifest


def parse_timestamp(value):
    if not isinstance(value, str):
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.utcoffset() is None:
        return None
    return moment.astimezone(timezone.utc)


def file_name_of(entry):
    return str(entry.get("file"))


def processing_order(entries):
    with_time, without_time = [], []
    for entry in entries:
        received_at = parse_timestamp(entry.get("received_at"))
        if received_at:
            with_time.append((received_at, entry))
        else:
            without_time.append((None, entry))
    with_time.sort(key=lambda item: (item[0], file_name_of(item[1])))
    without_time.sort(key=lambda item: file_name_of(item[1]))
    return with_time + without_time


def looks_like_relative_path(relative):
    return (isinstance(relative, str)
            and relative != ""
            and "\0" not in relative
            and not relative.startswith(("/", "\\"))
            and not os.path.isabs(relative)
            and not os.path.splitdrive(relative)[0])


def is_within(path, folder):
    return os.path.commonpath([folder, path]) == folder


def path_inside(inbox, relative):
    if not looks_like_relative_path(relative):
        return None
    path = os.path.normpath(os.path.join(inbox, relative))
    real_folder = os.path.realpath(os.path.dirname(path))
    if path == inbox or not is_within(path, inbox) or not is_within(real_folder, inbox):
        return None
    return path


def ignored_files(inbox, entries):
    referenced = set()
    for entry in entries:
        path = path_inside(inbox, entry.get("file"))
        if path:
            referenced.add(os.path.normcase(path))
    try:
        names = sorted(os.listdir(inbox))
    except OSError as error:
        raise EngineError(f"cannot list inbox {inbox}: {error}") from None
    ignored = []
    for name in names:
        path = os.path.join(inbox, name)
        if name != "manifest.json" and not os.path.isdir(path) and os.path.normcase(path) not in referenced:
            ignored.append(name)
    return ignored


def read_at_most(path, limit):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        data = b""
        while len(data) < limit:
            chunk = os.read(descriptor, limit - len(data))
            if not chunk:
                break
            data += chunk
        return data
    finally:
        os.close(descriptor)


def is_utf8(data):
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def reason_to_reject_file(entry, received_at, path, policy):
    if received_at is None:
        return "invalid_timestamp"
    if entry.get("channel") not in policy["channels"]:
        return "invalid_channel"
    if path is None:
        return "unsafe_path"
    if not os.path.lexists(path):
        return "missing_file"
    if os.path.islink(path):
        return "symlink"
    if not stat.S_ISREG(os.lstat(path).st_mode):
        return "unsafe_path"
    if os.path.splitext(path)[1].lower() not in policy["allowed_extensions"]:
        return "unsupported_type"
    if os.path.getsize(path) > policy["max_bytes"]:
        return "too_large"
    return None


def reason_to_reject_contents(data, policy):
    if data is None:
        return "unsafe_path"
    if len(data) > policy["max_bytes"]:
        return "too_large"
    if not data:
        return "empty"
    if not is_utf8(data):
        return "not_utf8"
    return None


def intake_file(entry, received_at, inbox, policy):
    path = path_inside(inbox, entry.get("file"))
    reason = reason_to_reject_file(entry, received_at, path, policy)
    if reason:
        return reason, None
    try:
        data = read_at_most(path, policy["max_bytes"] + 1)
    except OSError:
        return "missing_file", None
    reason = reason_to_reject_contents(data, policy)
    if reason:
        return reason, None
    return None, data
