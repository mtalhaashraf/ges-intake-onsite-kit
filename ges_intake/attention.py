"""Per-role attention lists (TASK §4.12). One truth underneath, different views on top.

The client view is built only from CEL client labels, dates and account endings: no internal
codes, nothing about other people, nothing about quarantine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ges_intake.config import Cel, Client, Library
from ges_intake.fields import to_json
from ges_intake.models import PENDING_REVIEW, QUARANTINED, Document
from ges_intake.readiness import (BLOCKED_BY_DEPENDENCY, MISSING, ONE_DAY, READY_FOR_DETERMINATION, Discontinuity,
                                  Gap, Readiness)

ROLES = ("client", "eic", "cpa", "owner")
LEVELS = ("NOW", "NEXT", "WATCH")
_LEVEL_BY_CRITICALITY = {"C1": "NOW", "C2": "NEXT", "C3": "NEXT"}


@dataclass(frozen=True)
class _Item:
    level: str
    kind: str
    key: str
    message: str


@dataclass
class _MissingEvidence:
    cel: Cel
    pdas: set[str] = field(default_factory=set)
    absent: bool = False
    period_gaps: list[Gap] = field(default_factory=list)
    account_gaps: dict[str, list[Gap]] = field(default_factory=dict)


def build(library: Library, client: Client, documents: list[Document], readiness: Readiness) -> dict:
    lists: dict[str, dict[str, _Item]] = {role: {} for role in ROLES}

    def add(roles: dict[str, str], kind: str, key: str, messages: dict[str, str]) -> None:
        for role, level in roles.items():
            lists[role].setdefault(key, _Item(level, kind, key, messages[role]))

    for evidence in _missing_evidence(library, readiness).values():
        level = _LEVEL_BY_CRITICALITY.get(evidence.cel.criticality)
        if level:
            add({"client": level, "eic": level}, "missing_evidence", f"missing:{evidence.cel.id}",
                {"client": _client_missing(evidence, client), "eic": _staff_missing(evidence, client)})

    holds: dict[str, set[str]] = {}
    for result in readiness.pdas:
        for issue in result.issues:
            holds.setdefault(issue, set()).add(result.pda.id)
    for doc in documents:
        if doc.status == PENDING_REVIEW:
            message = _review_document(doc, holds.get(f"document:{doc.document_id}", set()))
            add({"eic": "NOW", "cpa": "WATCH"}, "review_required", f"issue:document:{doc.document_id}",
                {"eic": message, "cpa": message})
    for result in readiness.pdas:
        for brk in result.discontinuities:
            message = _review_break(brk, holds.get(brk.issue, set()))
            add({"eic": "NOW", "cpa": "WATCH"}, "review_required", f"issue:{brk.issue}",
                {"eic": message, "cpa": message})

    for doc in documents:
        if doc.status == QUARANTINED:
            message = (f"{doc.document_id} ({doc.source}, batch {doc.batch_id}) belongs to another taxpayer: "
                       f"quarantined with no extracted fields kept. The service stays blocked until it is "
                       f"removed from this client's file and the incident is logged.")
            add({"eic": "NOW", "cpa": "WATCH", "owner": "NOW"}, "security_incident",
                f"issue:document:{doc.document_id}", {"eic": message, "cpa": message, "owner": message})

    for result in readiness.pdas:
        pda = result.pda
        if result.status == READY_FOR_DETERMINATION:
            add({"cpa": "NOW"}, "ready_for_determination", f"pda:{pda.id}",
                {"cpa": f"{pda.id} {pda.name}: evidence is in place ({', '.join(result.satisfied_by)}); "
                        f"your determination is needed."})
        elif result.status == BLOCKED_BY_DEPENDENCY:
            waiting = ", ".join(f"{dep.pda.id} ({dep.status})" for dep in result.blocking)
            add({"cpa": "WATCH"}, "blocked_by_dependency", f"pda:{pda.id}",
                {"cpa": f"{pda.id} {pda.name}: its own evidence is in place; waiting on {waiting}."})

    return {role: [_render(item, role) for item in sorted(
                items.values(), key=lambda i: (LEVELS.index(i.level), i.kind, i.key))]
            for role, items in lists.items()}


def _render(item: _Item, role: str) -> dict:
    if role == "client":
        return {"level": item.level, "kind": item.kind, "message": item.message}
    return {"level": item.level, "kind": item.kind, "key": item.key, "message": item.message}


def _missing_evidence(library: Library, readiness: Readiness) -> dict[str, _MissingEvidence]:
    """Missing CELs from the first pathway of every MISSING PDA, one entry per CEL with all its gaps."""
    merged: dict[str, _MissingEvidence] = {}
    for result in readiness.pdas:
        if result.status != MISSING or not result.pathways:
            continue
        for missing in result.pathways[0].missing:
            entry = merged.setdefault(missing.cel, _MissingEvidence(cel=library.cels[missing.cel]))
            entry.pdas.add(result.pda.id)
            if missing.gaps is None:
                entry.absent = True
            elif missing.account is None:
                entry.period_gaps.extend(missing.gaps)
            else:
                entry.account_gaps.setdefault(missing.account, []).extend(missing.gaps)
    for entry in merged.values():
        entry.period_gaps = _merge(entry.period_gaps)
        entry.account_gaps = {account: _merge(gaps) for account, gaps in entry.account_gaps.items()}
    return merged


def _merge(gaps: list[Gap]) -> list[Gap]:
    out: list[Gap] = []
    for start, end in sorted(gaps):
        if out and start <= out[-1][1] + ONE_DAY:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _ranges(gaps: list[Gap]) -> str:
    return ", ".join(_day(start) if start == end else f"{_day(start)} to {_day(end)}" for start, end in gaps)


def _day(value: date) -> str:
    return value.isoformat()


def _client_missing(evidence: _MissingEvidence, client: Client) -> str:
    parts = [f"the account ending {account} ({_ranges(evidence.account_gaps[account])})"
             for account in client.accounts if account in evidence.account_gaps]
    message = f"Please upload {evidence.cel.client_label}"
    if parts:
        message += " for " + (parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1])
    if evidence.period_gaps:
        message += f" covering {_ranges(evidence.period_gaps)}"
    return message + "."


def _staff_missing(evidence: _MissingEvidence, client: Client) -> str:
    details = [f"account ending {account}: {_ranges(evidence.account_gaps[account])}"
               for account in client.accounts if account in evidence.account_gaps]
    if evidence.period_gaps:
        details.append(f"period: {_ranges(evidence.period_gaps)}")
    message = (f"Follow up with the client: {evidence.cel.name} ({evidence.cel.id}) is outstanding for "
               f"{', '.join(sorted(evidence.pdas))}")
    return message + (f"; {'; '.join(details)}." if details else ".")


def _review_document(doc: Document, pdas: set[str]) -> str:
    what = doc.doc_type or "unclassified"
    message = f"Review {doc.document_id} ({doc.source}, {what}): flags {', '.join(sorted(doc.flags)) or 'none'}."
    if doc.advisory and doc.advisory.get("suggested_doc_type"):
        message += (f" Advisory suggestion, not applied: {doc.advisory['suggested_doc_type']}"
                    f" (confidence {doc.advisory.get('confidence')}).")
    if pdas:
        message += f" Holding {', '.join(sorted(pdas))}."
    return message


def _review_break(brk: Discontinuity, pdas: set[str]) -> str:
    previous, current = brk.previous, brk.current
    opening = to_json(current.fields.get("beginning_balance")) or "missing"
    closing = to_json(previous.fields.get("ending_balance")) or "missing"
    message = (f"Balance break on the account ending {brk.account}: {current.document_id} opens "
               f"{to_json(current.fields['period_start'])} at {opening}, but {previous.document_id} closed "
               f"{to_json(previous.fields['period_end'])} at {closing}.")
    if pdas:
        message += f" Holding {', '.join(sorted(pdas))}."
    return message
