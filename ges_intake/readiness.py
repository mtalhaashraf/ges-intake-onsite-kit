"""Readiness (TASK §4.9): is the evidence the engaged service needs present, usable and current?

A PDA is met if any pathway is met; a pathway is met if all its requirements are. The engine
reports what is missing precisely and never makes the CPA's determination itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ges_intake.config import Cel, Client, Library, Pda, Requirement
from ges_intake.models import ACCEPTED, PENDING_REVIEW, QUARANTINED, Document

MISSING = "MISSING"
PENDING = "PENDING_REVIEW"
BLOCKED_BY_DEPENDENCY = "BLOCKED_BY_DEPENDENCY"
READY_FOR_DETERMINATION = "READY_FOR_DETERMINATION"
SATISFIED = "SATISFIED"
DONE = (SATISFIED, READY_FOR_DETERMINATION)
ONE_DAY = timedelta(days=1)

Gap = tuple[date, date]


@dataclass(frozen=True)
class Missing:
    cel: str
    account: str | None = None
    gaps: tuple[Gap, ...] | None = None  # None: no coverage rule, the CEL is simply absent

    def report(self) -> dict:
        out: dict = {"cel": self.cel}
        if self.account is not None:
            out["account"] = self.account
        if self.gaps is not None:
            out["gaps"] = [[start.isoformat(), end.isoformat()] for start, end in self.gaps]
        return out


@dataclass(frozen=True)
class Discontinuity:
    issue: str
    account: str
    previous: Document
    current: Document


@dataclass
class RequirementResult:
    missing: list[Missing]
    usable: list[Document]
    discontinuities: list[Discontinuity] = field(default_factory=list)

    @property
    def met(self) -> bool:
        return not self.missing


@dataclass
class PathwayResult:
    name: str
    met: bool
    missing: list[Missing]
    usable_ids: set[str]


@dataclass
class PdaResult:
    pda: Pda
    status: str
    pathways: list[PathwayResult]
    satisfied_by: list[str]
    issues: list[str]
    discontinuities: list[Discontinuity]
    blocking: list[PdaResult]  # dependencies that are not done


@dataclass
class Readiness:
    service: str
    status: str
    security_holds: list[str]
    pdas: list[PdaResult]


def evaluate(library: Library, client: Client, documents: list[Document], as_of: date) -> Readiness:
    service = library.services[client.service]
    evaluator = _Evaluator(library, client, documents, as_of)
    by_id = {pda.id: pda for pda in service.pdas}
    results: dict[str, PdaResult] = {}

    def resolve(pda: Pda) -> PdaResult:
        if pda.id not in results:  # the library loader has already rejected cycles
            deps = [resolve(by_id[dep]) for dep in pda.depends_on]
            results[pda.id] = evaluator.pda(pda, deps)
        return results[pda.id]

    pdas = [resolve(pda) for pda in service.pdas]
    holds = sorted(f"document:{d.document_id}" for d in documents if d.status == QUARANTINED)
    if holds:
        status = "BLOCKED"
    elif all(result.status in DONE for result in pdas):
        status = "READY"
    else:
        status = "NOT_READY"
    return Readiness(service=service.id, status=status, security_holds=holds, pdas=pdas)


def report(readiness: Readiness, client: Client, library: Library, as_of: date) -> dict:
    return {
        "client_id": client.id,
        "service": readiness.service,
        "as_of": as_of.isoformat(),
        "library_version": library.version,
        "status": readiness.status,
        "security_holds": readiness.security_holds,
        "pdas": [{
            "id": result.pda.id,
            "name": result.pda.name,
            "status": result.status,
            "depends_on": list(result.pda.depends_on),
            "pathways": [{"name": p.name, "met": p.met, "missing": [m.report() for m in p.missing]}
                         for p in result.pathways],
            "satisfied_by": result.satisfied_by,
            "issues": result.issues,
        } for result in readiness.pdas],
    }


class _Evaluator:
    def __init__(self, library: Library, client: Client, documents: list[Document], as_of: date):
        self.library = library
        self.client = client
        self.as_of = as_of
        self.evidence = [d for d in documents if d.status == ACCEPTED]
        self.pending = [d for d in documents if d.status == PENDING_REVIEW]

    def pda(self, pda: Pda, deps: list[PdaResult]) -> PdaResult:
        pathways: list[PathwayResult] = []
        discontinuities: dict[str, Discontinuity] = {}
        for pathway in pda.pathways:
            results = [self.requirement(req) for req in pathway.requirements]
            pathways.append(PathwayResult(
                name=pathway.name,
                met=all(r.met for r in results),
                missing=[m for r in results for m in r.missing],
                usable_ids={d.document_id for r in results for d in r.usable},
            ))
            for r in results:
                for d in r.discontinuities:
                    discontinuities.setdefault(d.issue, d)

        # A pending document stops the determination even if another pathway is met:
        # someone must look at it first.
        cels = {req.cel for pathway in pda.pathways for req in pathway.requirements}
        issues = set(discontinuities) | {f"document:{d.document_id}" for d in self.pending if d.cel in cels}
        first_met = next((p for p in pathways if p.met), None)
        blocking = [dep for dep in deps if dep.status not in DONE]
        if first_met is None:
            status = MISSING
        elif issues:
            status = PENDING
        elif blocking:
            status = BLOCKED_BY_DEPENDENCY
        else:
            status = READY_FOR_DETERMINATION if pda.requires_cpa else SATISFIED
        return PdaResult(
            pda=pda,
            status=status,
            pathways=pathways,
            satisfied_by=sorted(first_met.usable_ids) if first_met else [],
            issues=sorted(issues),
            discontinuities=[discontinuities[key] for key in sorted(discontinuities)],
            blocking=blocking,
        )

    def requirement(self, req: Requirement) -> RequirementResult:
        cel = self.library.cels[req.cel]
        usable = [d for d in self.evidence if self._usable(d, req, cel)]
        if req.coverage is None:
            return RequirementResult(missing=[] if usable else [Missing(cel=req.cel)], usable=usable)
        if req.coverage == "engagement_period":
            gaps = self._gaps(usable)
            return RequirementResult(missing=[Missing(cel=req.cel, gaps=gaps)] if gaps else [], usable=usable)

        missing, used, breaks = [], [], []
        for account in self.client.accounts:
            docs = [d for d in usable if self._account(d) == account]
            gaps = self._gaps(docs)
            if gaps:
                missing.append(Missing(cel=req.cel, account=account, gaps=gaps))
            used.extend(docs)
            breaks.extend(_continuity(account, docs))
        return RequirementResult(missing=missing, usable=used, discontinuities=breaks)

    def _usable(self, doc: Document, req: Requirement, cel: Cel) -> bool:
        return (doc.cel == req.cel
                and doc.grade in req.accepted_grades
                and doc.provenance in cel.allowed_provenance
                and (req.provenance is None or doc.provenance in req.provenance)
                and self._current(doc, cel))

    def _current(self, doc: Document, cel: Cel) -> bool:
        if cel.currentness == "none":
            return True
        if cel.currentness == "not_expired":
            expiration = doc.fields.get("expiration")
            return isinstance(expiration, date) and expiration >= self.as_of
        if cel.currentness == "tax_year":
            return doc.fields.get("tax_year") == self.client.period_end.year + cel.offset
        period = _period(doc)
        return period is not None and period[0] <= self.client.period_end and period[1] >= self.client.period_start

    def _account(self, doc: Document) -> object:
        doc_type = self.library.doc_type(doc.doc_type)
        return doc.fields.get(doc_type.account_field or "account")

    def _gaps(self, docs: list[Document]) -> tuple[Gap, ...]:
        """Inclusive, ascending, merged ranges of the engagement period no document covers."""
        start, end = self.client.period_start, self.client.period_end
        gaps, cursor = [], start
        for first, last in sorted(p for p in map(_period, docs) if p is not None):
            if last < cursor:
                continue
            if first > end:
                break
            if first > cursor:
                gaps.append((cursor, first - ONE_DAY))
            cursor = min(last, end) + ONE_DAY
            if cursor > end:
                break
        if cursor <= end:
            gaps.append((cursor, end))
        return tuple(gaps)


def _period(doc: Document) -> Gap | None:
    start, end = doc.fields.get("period_start"), doc.fields.get("period_end")
    if isinstance(start, date) and isinstance(end, date):
        return start, end
    return None


def _continuity(account: str, docs: list[Document]):
    """Compare each statement's opening balance with the one immediately before it.
    Only directly adjacent periods are compared; a missing balance counts as a break."""
    dated = sorted((d for d in docs if _period(d)), key=lambda d: (*_period(d), d.document_id))
    for previous, current in zip(dated, dated[1:]):
        if _period(current)[0] != _period(previous)[1] + ONE_DAY:
            continue
        ending, beginning = previous.fields.get("ending_balance"), current.fields.get("beginning_balance")
        if ending is None or beginning is None or ending != beginning:
            yield Discontinuity(issue=f"balance_discontinuity:{account}:{_period(current)[0].isoformat()}",
                                account=account, previous=previous, current=current)
