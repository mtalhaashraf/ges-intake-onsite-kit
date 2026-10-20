import json
import os
import re
from dataclasses import dataclass
from datetime import date

from .govern import PARSERS, digits_only, phrase_view

VALID_CLIENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
CURRENTNESS_RULES = {"none", "not_expired", "tax_year", "period_overlap"}
COVERAGE_KINDS = {None, "engagement_period", "engagement_period_per_account"}


class EngineError(Exception):
    pass


@dataclass
class Library:
    version: str
    cels: dict
    document_types: dict
    policy: dict
    suspicious_patterns: list
    services: dict


@dataclass
class Client:
    id: str
    name: str
    ssn: str
    accounts: list
    service: dict
    period_start: date
    period_end: date


def read_json(path):
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def fail_unless(condition, message):
    if not condition:
        raise EngineError(f"invalid library: {message}")


def all_requirements(pda):
    return [requirement for pathway in pda["pathways"] for requirement in pathway["requirements"]]


def in_dependency_order(pdas):
    waiting = {pda["id"]: set(pda.get("depends_on", [])) for pda in pdas}
    order = []
    while waiting:
        ready = [pda_id for pda_id, dependencies in waiting.items() if not dependencies & waiting.keys()]
        if not ready:
            return None
        order += ready
        for pda_id in ready:
            del waiting[pda_id]
    return order


def load_library(folder):
    try:
        library = read_library(folder)
        check_references(library)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, re.error) as error:
        raise EngineError(f"invalid library: {error!r}") from None
    return library


def read_library(folder):
    cel_file = read_json(os.path.join(folder, "cel.json"))
    policy = read_json(os.path.join(folder, "intake_policy.json"))
    policy["allowed_extensions"] = [extension.lower() for extension in policy["allowed_extensions"]]
    document_types = {}
    for doc_type in read_json(os.path.join(folder, "document_types.json"))["document_types"]:
        document_types[doc_type["id"]] = prepare_document_type(doc_type)
    return Library(
        version=cel_file.get("library_version"),
        cels={cel["id"]: cel for cel in cel_file["cel"]},
        document_types=document_types,
        policy=policy,
        suspicious_patterns=[re.compile(p, re.IGNORECASE) for p in policy["suspicious_instruction_patterns"]],
        services={service["id"]: service for service in read_json(os.path.join(folder, "services.json"))["services"]},
    )


def prepare_document_type(doc_type):
    doc_type["must_contain"] = [phrase_view(phrase) for phrase in doc_type["match"]["all"]]
    doc_type["must_not_contain"] = [phrase_view(phrase) for phrase in doc_type["match"].get("none", [])]
    for field in doc_type["fields"].values():
        field["regex"] = re.compile(field["pattern"], re.MULTILINE | re.IGNORECASE)
    return doc_type


def check_references(library):
    for cel in library.cels.values():
        fail_unless(cel["currentness"]["rule"] in CURRENTNESS_RULES, f"{cel['id']} has an unknown currentness rule")
    for doc_type in library.document_types.values():
        fail_unless(doc_type["cel"] in library.cels, f"{doc_type['id']} uses unknown CEL {doc_type['cel']}")
        for name, field in doc_type["fields"].items():
            fail_unless(field["type"] in PARSERS or field["type"] == "date_range",
                        f"{doc_type['id']}.{name} has unknown type {field['type']}")
        for replaced in doc_type.get("supersede", {}).get("supersedes", []):
            fail_unless(replaced in library.document_types, f"{doc_type['id']} supersedes unknown type {replaced}")
    for service in library.services.values():
        pda_ids = {pda["id"] for pda in service["pdas"]}
        for pda in service["pdas"]:
            for requirement in all_requirements(pda):
                fail_unless(requirement["cel"] in library.cels, f"{pda['id']} uses unknown CEL {requirement['cel']}")
                fail_unless(requirement.get("coverage") in COVERAGE_KINDS, f"{pda['id']} has unknown coverage")
            for dependency in pda.get("depends_on", []):
                fail_unless(dependency in pda_ids, f"{pda['id']} depends on unknown PDA {dependency}")
        fail_unless(in_dependency_order(service["pdas"]) is not None, f"service {service['id']} has a dependency cycle")


def load_client(folder, client_id, library):
    if not VALID_CLIENT_ID.fullmatch(client_id):
        raise EngineError(f"malformed client id {client_id!r}")
    file_name = f"{client_id}.json"
    try:
        if file_name not in os.listdir(folder):
            raise EngineError(f"unknown client {client_id}")
        profile = read_json(os.path.join(folder, file_name))
        engagement = profile["engagement"]
        client = Client(
            id=profile["client_id"],
            name=profile["name"],
            ssn=digits_only(profile["ssn"]),
            accounts=[str(account) for account in profile["accounts"]],
            service=library.services[engagement["service"]],
            period_start=date.fromisoformat(engagement["period_start"]),
            period_end=date.fromisoformat(engagement["period_end"]),
        )
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise EngineError(f"invalid client {client_id}: {type(error).__name__}") from None
    if client.id != client_id or len(client.ssn) != 9 or client.period_start > client.period_end:
        raise EngineError(f"invalid client {client_id}")
    return client
