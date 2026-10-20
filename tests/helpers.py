"""Shared fixtures: generated inboxes, a copy of the library, and a subprocess runner."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Workspace:
    """A temporary directory with a freshly generated inbox and copies of library/ and clients/."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name)
        self.inbox = self.path / "inbox"
        self.library = self.path / "library"
        self.clients = self.path / "clients"
        self.outbox = self.path / "outbox.jsonl"
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_inbox.py"), str(self.inbox)],
                       check=True, capture_output=True)
        shutil.copytree(ROOT / "library", self.library)
        shutil.copytree(ROOT / "clients", self.clients)

    def close(self):
        self._tmp.cleanup()

    def run(self, *inboxes, client="C-1001", as_of="2026-03-01", env=None):
        args = [sys.executable, "-m", "ges_intake", "--library", str(self.library), "--clients", str(self.clients),
                "run", "--client", client, "--as-of", as_of, "--outbox", str(self.outbox)]
        for inbox in inboxes or ("day1",):
            args += ["--inbox", str(self.inbox / inbox)]
        return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env={**os.environ, **(env or {})})

    def report(self, *inboxes, **kwargs):
        result = self.run(*inboxes, **kwargs)
        if result.returncode != 0:
            raise AssertionError(f"exit {result.returncode}: {result.stderr}")
        return json.loads(result.stdout)

    def edit_json(self, path: Path, change):
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data), encoding="utf-8")

    def outbox_text(self):
        return self.outbox.read_text(encoding="utf-8") if self.outbox.exists() else ""


def by_source(batch: dict, source: str) -> dict:
    return next(d for d in batch["documents"] if d["source"] == source)


def pda(report: dict, pda_id: str) -> dict:
    return next(p for p in report["readiness"]["pdas"] if p["id"] == pda_id)
