"""Evidence library and client profile loading.

Everything the engine knows about CEL kinds, document types, services and intake rules comes
from these files; nothing about a particular service, PDA, CEL or document type is hard-coded.
Validation is strict: an invalid library or client profile stops the run (exit 2) before any
inbox is touched.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ges_intake.errors import InputError
from ges_intake.fields import parse_account, parse_iso_date, parse_ssn
from ges_intake.text import phrase_key

FIELD_TYPES = frozenset({"text", "date", "date_range", "money", "year", "ssn", "ein", "account"})
CURRENTNESS_RULES = frozenset({"none", "not_expired", "tax_year", "period_overlap"})
COVERAGE_RULES = frozenset({"engagement_period", "engagement_period_per_account"})
PROVENANCE_CODES = frozenset({"P1", "P2", "P3", "P4"})
GRADES = frozenset({"E1", "E2", "E3", "E4", "E5"})
CRITICALITIES = frozenset({"C1", "C2", "C3", "C4"})

# A client ID becomes a path fragment, so it is checked before it is used: no dots, no
# separators, ASCII only. The profile must then also carry exactly this ID (see load_client).
CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    regex: re.Pattern
    type: str

    @property
    def persisted_names(self) -> tuple[str, ...]:
        if self.type == "date_range":
            return (f"{self.name}_start", f"{self.name}_end")
        return (self.name,)


@dataclass(frozen=True)
class DocType:
    id: str
    cel: str
    match_all: tuple[str, ...]
    match_none: tuple[str, ...]
    provenance_default: str
    provenance_by_channel: dict[str, str]
    claims_official: bool
    fields: tuple[FieldSpec, ...]
    required_fields: tuple[str, ...]
    ssn_field: str | None
    name_field: str | None
    account_field: str | None
    supersede_key: tuple[str, ...] | None
    supersedes: frozenset[str]

    def field(self, name: str) -> FieldSpec:
        return next(spec for spec in self.fields if spec.name == name)


@dataclass(frozen=True)
class Cel:
    id: str
    name: str
    client_label: str
    criticality: str
    allowed_provenance: frozenset[str]
    currentness: str
    offset: int


@dataclass(frozen=True)
class Requirement:
    cel: str
    accepted_grades: frozenset[str]
    provenance: frozenset[str] | None
    coverage: str | None


@dataclass(frozen=True)
class Pathway:
    name: str
    requirements: tuple[Requirement, ...]


@dataclass(frozen=True)
class Pda:
    id: str
    name: str
    requires_cpa: bool
    depends_on: tuple[str, ...]
    pathways: tuple[Pathway, ...]


@dataclass(frozen=True)
class Service:
    id: str
    name: str
    pdas: tuple[Pda, ...]


@dataclass(frozen=True)
class Policy:
    max_bytes: int
    allowed_extensions: frozenset[str]
    channels: frozenset[str]
    suspicious_patterns: tuple[re.Pattern, ...]
    review_flags: frozenset[str]
    e4_flags: frozenset[str]


@dataclass(frozen=True)
class Library:
    version: str
    cels: dict[str, Cel]
    doc_types: tuple[DocType, ...]
    services: dict[str, Service]
    policy: Policy

    def doc_type(self, doc_type_id: str) -> DocType:
        return next(dt for dt in self.doc_types if dt.id == doc_type_id)


@dataclass(frozen=True)
class Client:
    id: str
    name: str
    ssn_last4: str
    ssn_full: str | None
    accounts: tuple[str, ...]  # last four digits, profile order
    service: str
    period_start: date
    period_end: date

    def __repr__(self) -> str:  # never let the SSN reach a log line or a traceback
        return f"Client(id={self.id!r})"


_MISSING = object()


class _Checker:
    """Small helpers that turn a shape problem into an InputError naming where it is."""

    def __init__(self, prefix: str):
        self.prefix = prefix

    def fail(self, where: str, problem: str) -> InputError:
        return InputError(f"{self.prefix}: {where}: {problem}")

    def obj(self, value, where: str) -> dict:
        if not isinstance(value, dict):
            raise self.fail(where, "expected an object")
        return value

    def get(self, obj: dict, key: str, kind: type, where: str, default=_MISSING):
        if key not in obj:
            if default is _MISSING:
                raise self.fail(where, f"missing '{key}'")
            return default
        value = obj[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise self.fail(where, f"'{key}' must be of type {kind.__name__}")
        return value

    def strings(self, obj: dict, key: str, where: str, default=_MISSING) -> tuple[str, ...]:
        value = self.get(obj, key, list, where, default)
        if value is default and default is not _MISSING:
            return tuple(value)
        if not all(isinstance(item, str) for item in value):
            raise self.fail(where, f"'{key}' must be a list of strings")
        return tuple(value)

    def codes(self, obj: dict, key: str, allowed: frozenset[str], where: str, default=_MISSING) -> frozenset[str]:
        values = self.strings(obj, key, where, default)
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise self.fail(where, f"'{key}' has unknown codes {unknown}")
        return frozenset(values)

    def read_json(self, path: Path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except OSError as exc:
            raise self.fail(path.name, f"cannot read ({exc.strerror or exc})") from None
        except ValueError as exc:
            raise self.fail(path.name, f"not valid JSON ({exc})") from None

    def regex(self, pattern: str, flags: int, where: str) -> re.Pattern:
        try:
            return re.compile(pattern, flags)
        except re.error as exc:
            raise self.fail(where, f"bad regular expression ({exc})") from None


def load_library(directory: Path) -> Library:
    check = _Checker("invalid library")
    policy = _load_policy(check, check.obj(check.read_json(directory / "intake_policy.json"), "intake_policy.json"))
    cel_doc = check.obj(check.read_json(directory / "cel.json"), "cel.json")
    version = check.get(cel_doc, "library_version", str, "cel.json")
    cels = _load_cels(check, check.get(cel_doc, "cel", list, "cel.json"))
    types_doc = check.obj(check.read_json(directory / "document_types.json"), "document_types.json")
    doc_types = _load_doc_types(check, check.get(types_doc, "document_types", list, "document_types.json"), cels, policy)
    services_doc = check.obj(check.read_json(directory / "services.json"), "services.json")
    services = _load_services(check, check.get(services_doc, "services", list, "services.json"), cels)
    return Library(version=version, cels=cels, doc_types=doc_types, services=services, policy=policy)


def _load_policy(check: _Checker, raw: dict) -> Policy:
    where = "intake_policy.json"
    max_bytes = check.get(raw, "max_bytes", int, where)
    if max_bytes < 0:
        raise check.fail(where, "'max_bytes' must not be negative")
    extensions = frozenset(ext.lower() if ext.startswith(".") else f".{ext.lower()}"
                           for ext in check.strings(raw, "allowed_extensions", where))
    patterns = tuple(check.regex(p, re.IGNORECASE, f"{where} suspicious_instruction_patterns")
                     for p in check.strings(raw, "suspicious_instruction_patterns", where, []))
    return Policy(
        max_bytes=max_bytes,
        allowed_extensions=extensions,
        channels=frozenset(check.strings(raw, "channels", where)),
        suspicious_patterns=patterns,
        review_flags=frozenset(check.strings(raw, "review_flags", where)),
        e4_flags=frozenset(check.strings(raw, "e4_flags", where)),
    )


def _load_cels(check: _Checker, raw_list: list) -> dict[str, Cel]:
    cels: dict[str, Cel] = {}
    for index, raw in enumerate(raw_list):
        where = f"cel.json cel[{index}]"
        raw = check.obj(raw, where)
        cel_id = check.get(raw, "id", str, where)
        if cel_id in cels:
            raise check.fail(where, f"duplicate CEL id {cel_id}")
        currentness = check.obj(check.get(raw, "currentness", dict, where), where)
        rule = check.get(currentness, "rule", str, where)
        if rule not in CURRENTNESS_RULES:
            raise check.fail(where, f"unknown currentness rule {rule!r}")
        criticality = check.get(raw, "criticality", str, where, "C1")
        if criticality not in CRITICALITIES:
            raise check.fail(where, f"unknown criticality {criticality!r}")
        name = check.get(raw, "name", str, where, cel_id)
        cels[cel_id] = Cel(
            id=cel_id,
            name=name,
            client_label=check.get(raw, "client_label", str, where, name),
            criticality=criticality,
            allowed_provenance=check.codes(raw, "allowed_provenance", PROVENANCE_CODES, where),
            currentness=rule,
            offset=check.get(currentness, "offset", int, where, 0),
        )
    return cels


def _load_doc_types(check: _Checker, raw_list: list, cels: dict[str, Cel], policy: Policy) -> tuple[DocType, ...]:
    doc_types: list[DocType] = []
    for index, raw in enumerate(raw_list):
        where = f"document_types.json document_types[{index}]"
        raw = check.obj(raw, where)
        type_id = check.get(raw, "id", str, where)
        where = f"document type {type_id}"
        if any(dt.id == type_id for dt in doc_types):
            raise check.fail(where, "duplicate document type id")
        cel = check.get(raw, "cel", str, where)
        if cel not in cels:
            raise check.fail(where, f"unknown CEL {cel}")

        match = check.obj(check.get(raw, "match", dict, where), where)
        provenance = check.obj(check.get(raw, "provenance", dict, where), where)
        default = check.get(provenance, "default", str, where)
        by_channel = check.obj(check.get(provenance, "by_channel", dict, where, {}), where)
        if default not in PROVENANCE_CODES or any(code not in PROVENANCE_CODES for code in by_channel.values()):
            raise check.fail(where, "unknown provenance code")
        unknown_channels = sorted(set(by_channel) - policy.channels)
        if unknown_channels:
            raise check.fail(where, f"by_channel names unknown channels {unknown_channels}")

        fields = []
        for name, spec in check.obj(check.get(raw, "fields", dict, where, {}), where).items():
            spec = check.obj(spec, f"{where} field {name}")
            ftype = check.get(spec, "type", str, f"{where} field {name}")
            if ftype not in FIELD_TYPES:
                raise check.fail(f"{where} field {name}", f"unknown field type {ftype!r}")
            regex = check.regex(check.get(spec, "pattern", str, f"{where} field {name}"),
                                re.MULTILINE | re.IGNORECASE, f"{where} field {name}")
            if regex.groups < (2 if ftype == "date_range" else 1):
                raise check.fail(f"{where} field {name}", "pattern has too few capture groups")
            fields.append(FieldSpec(name=name, regex=regex, type=ftype))
        by_name = {spec.name: spec for spec in fields}

        required = check.strings(raw, "required_fields", where, [])
        if any(name not in by_name for name in required):
            raise check.fail(where, "required_fields names an undefined field")
        identity = check.obj(check.get(raw, "identity", dict, where, {}), where)
        roles = {}
        for role, ftype in (("ssn_field", "ssn"), ("name_field", "text"), ("account_field", "account")):
            name = check.get(identity, role, str, where, None)
            if name is not None and (name not in by_name or by_name[name].type != ftype):
                raise check.fail(where, f"identity.{role} must name a field of type {ftype}")
            roles[role] = name

        supersede_key, supersedes = None, frozenset()
        if "supersede" in raw:
            supersede = check.obj(raw["supersede"], f"{where} supersede")
            supersede_key = check.strings(supersede, "key", f"{where} supersede")
            persisted = {p for spec in fields for p in spec.persisted_names}
            if not supersede_key or any(key not in persisted for key in supersede_key):
                raise check.fail(where, "supersede.key must name extracted fields")
            supersedes = frozenset(check.strings(supersede, "supersedes", f"{where} supersede"))

        doc_types.append(DocType(
            id=type_id,
            cel=cel,
            match_all=tuple(phrase_key(p) for p in check.strings(match, "all", where)),
            match_none=tuple(phrase_key(p) for p in check.strings(match, "none", where, [])),
            provenance_default=default,
            provenance_by_channel=dict(by_channel),
            claims_official=check.get(provenance, "claims_official", bool, where, False),
            fields=tuple(fields),
            required_fields=required,
            supersede_key=supersede_key,
            supersedes=supersedes,
            **roles,
        ))
    known = {dt.id for dt in doc_types}
    for dt in doc_types:
        if dt.supersedes - known:
            raise check.fail(f"document type {dt.id}", f"supersedes unknown types {sorted(dt.supersedes - known)}")
    return tuple(doc_types)


def _load_services(check: _Checker, raw_list: list, cels: dict[str, Cel]) -> dict[str, Service]:
    services: dict[str, Service] = {}
    for index, raw in enumerate(raw_list):
        where = f"services.json services[{index}]"
        raw = check.obj(raw, where)
        service_id = check.get(raw, "id", str, where)
        if service_id in services:
            raise check.fail(where, f"duplicate service id {service_id}")
        pdas: list[Pda] = []
        for pda_index, pda_raw in enumerate(check.get(raw, "pdas", list, where)):
            pda_where = f"service {service_id} pdas[{pda_index}]"
            pda_raw = check.obj(pda_raw, pda_where)
            pda_id = check.get(pda_raw, "id", str, pda_where)
            if any(p.id == pda_id for p in pdas):
                raise check.fail(pda_where, f"duplicate PDA id {pda_id}")
            pdas.append(Pda(
                id=pda_id,
                name=check.get(pda_raw, "name", str, pda_where),
                requires_cpa=check.get(pda_raw, "requires_cpa", bool, pda_where),
                depends_on=check.strings(pda_raw, "depends_on", pda_where, []),
                pathways=tuple(_load_pathway(check, p, f"{pda_where} pathways[{i}]", cels)
                               for i, p in enumerate(check.get(pda_raw, "pathways", list, pda_where))),
            ))
        _check_dependencies(check, service_id, pdas)
        services[service_id] = Service(id=service_id, name=check.get(raw, "name", str, where, service_id),
                                       pdas=tuple(pdas))
    return services


def _load_pathway(check: _Checker, raw, where: str, cels: dict[str, Cel]) -> Pathway:
    raw = check.obj(raw, where)
    requirements = []
    for index, req in enumerate(check.get(raw, "requirements", list, where)):
        req_where = f"{where} requirements[{index}]"
        req = check.obj(req, req_where)
        cel = check.get(req, "cel", str, req_where)
        if cel not in cels:
            raise check.fail(req_where, f"unknown CEL {cel}")
        coverage = check.get(req, "coverage", str, req_where, None)
        if coverage is not None and coverage not in COVERAGE_RULES:
            raise check.fail(req_where, f"unknown coverage {coverage!r}")
        grades = check.codes(req, "accepted_grades", GRADES, req_where)
        if not grades:
            raise check.fail(req_where, "accepted_grades is empty")
        provenance = (check.codes(req, "provenance", PROVENANCE_CODES, req_where)
                      if "provenance" in req else None)
        requirements.append(Requirement(cel=cel, accepted_grades=grades, provenance=provenance, coverage=coverage))
    if not requirements:
        # "All of nothing" would make the pathway met with no evidence at all.
        raise check.fail(where, "a pathway needs at least one requirement")
    return Pathway(name=check.get(raw, "name", str, where), requirements=tuple(requirements))


def _check_dependencies(check: _Checker, service_id: str, pdas: list[Pda]) -> None:
    by_id = {pda.id: pda for pda in pdas}
    for pda in pdas:
        unknown = [dep for dep in pda.depends_on if dep not in by_id]
        if unknown:
            raise check.fail(f"service {service_id} {pda.id}", f"depends on unknown PDAs {unknown}")
    state: dict[str, str] = {}

    def visit(pda_id: str, path: list[str]) -> None:
        if state.get(pda_id) == "done":
            return
        if state.get(pda_id) == "active":
            cycle = path[path.index(pda_id):] + [pda_id]
            raise check.fail(f"service {service_id}", f"dependency cycle {' -> '.join(cycle)}")
        state[pda_id] = "active"
        for dep in by_id[pda_id].depends_on:
            visit(dep, path + [pda_id])
        state[pda_id] = "done"

    for pda in pdas:
        visit(pda.id, [])


def load_client(directory: Path, client_id: str, library: Library) -> Client:
    if not CLIENT_ID.fullmatch(client_id):
        raise InputError("malformed client ID")
    path = directory / f"{client_id}.json"
    if not path.is_file():
        raise InputError(f"unknown client {client_id}")
    check = _Checker(f"invalid client profile {client_id}")
    raw = check.obj(check.read_json(path), path.name)
    if raw.get("client_id") != client_id:
        # Also catches case-insensitive file systems resolving "c-1001" to C-1001.json.
        raise InputError(f"unknown client {client_id}")
    where = path.name
    ssn = parse_ssn(check.get(raw, "ssn", str, where))
    if ssn is None:
        raise check.fail(where, "ssn is not in a recognised form")
    accounts = []
    for account in check.strings(raw, "accounts", where, []):
        last4 = parse_account(account)
        if last4 is None:
            raise check.fail(where, "every account needs at least four digits")
        if last4 in accounts:
            raise check.fail(where, "two accounts share the same last four digits")
        accounts.append(last4)
    engagement = check.obj(check.get(raw, "engagement", dict, where), where)
    service = check.get(engagement, "service", str, where)
    if service not in library.services:
        raise check.fail(where, f"engaged service {service} is not in the library")
    start = parse_iso_date(engagement.get("period_start"))
    end = parse_iso_date(engagement.get("period_end"))
    if start is None or end is None or end < start:
        raise check.fail(where, "engagement period must be two YYYY-MM-DD dates in order")
    name = check.get(raw, "name", str, where)
    if not name.strip():
        raise check.fail(where, "name is empty")
    return Client(id=client_id, name=name, ssn_last4=ssn.last4, ssn_full=ssn.full, accounts=tuple(accounts),
                  service=service, period_start=start, period_end=end)
