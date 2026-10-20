"""The external advisory classifier (TASK §4.13).

Text sent here leaves the firm, so it is scrubbed first. The answer is recorded for the
reviewer and is never applied: nothing in this module can change a classification,
provenance, grade, status or readiness.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from ges_intake.config import Client
from ges_intake.text import name_tokens, scrub_identifiers

_stub = None


def request(text: str, client: Client, outbox: str) -> dict:
    payload = scrub_identifiers(text, names=tuple(name_tokens(client.name)))
    try:
        answer = _vendor().request_suggestion(payload, outbox)
    except OSError:
        return {"applied": False, "error": "advisory service unavailable"}
    if not isinstance(answer, dict):
        answer = {}
    return {
        "applied": False,
        "suggested_doc_type": _typed(answer.get("doc_type"), str),
        "confidence": _typed(answer.get("confidence"), (int, float)),
        "rationale": _typed(answer.get("rationale"), str),
    }


def _typed(value, kinds):
    return value if isinstance(value, kinds) and not isinstance(value, bool) else None


def _vendor():
    """The stub lives outside the package; import it from the repository root wherever we run from."""
    global _stub
    if _stub is None:
        try:
            from vendor import advisory_stub
        except ImportError:
            path = Path(__file__).resolve().parent.parent / "vendor" / "advisory_stub.py"
            spec = importlib.util.spec_from_file_location("vendor.advisory_stub", path)
            advisory_stub = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(advisory_stub)
        _stub = advisory_stub
    return _stub
