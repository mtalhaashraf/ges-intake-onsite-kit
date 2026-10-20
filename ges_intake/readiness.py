from datetime import date, timedelta

from .library import all_requirements, in_dependency_order

ONE_DAY = timedelta(days=1)
DONE = {"SATISFIED", "READY_FOR_DETERMINATION"}


def mark_superseded_documents(docs, library):
    for older in docs:
        older.superseded = any(replaces(newer, older, library) for newer in docs)


def key_value(doc, name):
    return doc.full_identifiers.get(name, doc.fields.get(name))


def replaces(newer, older, library):
    if newer is older or newer.doc_type is None:
        return False
    if "quarantined" in (newer.base_status, older.base_status):
        return False
    rule = library.document_types[newer.doc_type].get("supersede")
    if not rule or older.doc_type not in rule["supersedes"]:
        return False
    for key in rule["key"]:
        if key not in newer.fields or key_value(older, key) != key_value(newer, key):
            return False
    return (newer.received_at, newer.document_id) > (older.received_at, older.document_id)


def has_period(doc):
    return "period_start" in doc.fields and "period_end" in doc.fields


def is_current(doc, rule, client, as_of):
    fields = doc.fields
    if rule["rule"] == "none":
        return True
    if rule["rule"] == "not_expired":
        return isinstance(fields.get("expiration"), date) and fields["expiration"] >= as_of
    if rule["rule"] == "tax_year":
        return fields.get("tax_year") == client.period_end.year + rule.get("offset", 0)
    if rule["rule"] == "period_overlap":
        return (has_period(doc)
                and fields["period_start"] <= client.period_end
                and fields["period_end"] >= client.period_start)
    return False


def is_usable(doc, requirement, cel, client, as_of):
    allowed_by_requirement = requirement.get("provenance", cel["allowed_provenance"])
    return (doc.status == "accepted"
            and doc.cel == requirement["cel"]
            and doc.grade in requirement["accepted_grades"]
            and doc.provenance in cel["allowed_provenance"]
            and doc.provenance in allowed_by_requirement
            and is_current(doc, cel["currentness"], client, as_of))


def find_gaps(docs, client):
    periods = sorted((doc.fields["period_start"], doc.fields["period_end"]) for doc in docs if has_period(doc))
    gaps = []
    first_uncovered_day = client.period_start
    for start, end in periods:
        if start > first_uncovered_day and first_uncovered_day <= client.period_end:
            gaps.append((first_uncovered_day, min(start - ONE_DAY, client.period_end)))
        first_uncovered_day = max(first_uncovered_day, end + ONE_DAY)
    if first_uncovered_day <= client.period_end:
        gaps.append((first_uncovered_day, client.period_end))
    return [[start.isoformat(), end.isoformat()] for start, end in gaps]


def follows_directly(previous, following):
    return following.fields["period_start"] == previous.fields["period_end"] + ONE_DAY


def balances_disagree(previous, following):
    closing = previous.fields.get("ending_balance")
    opening = following.fields.get("beginning_balance")
    return closing is not None and opening is not None and closing != opening


def balance_breaks(account, statements):
    ordered = sorted((doc for doc in statements if has_period(doc)),
                     key=lambda doc: (doc.fields["period_start"], doc.fields["period_end"], doc.document_id))
    breaks = set()
    for previous, following in zip(ordered, ordered[1:]):
        if follows_directly(previous, following) and balances_disagree(previous, following):
            breaks.add(f"balance_discontinuity:{account}:{following.fields['period_start'].isoformat()}")
    return breaks


def check_each_account(cel_id, usable, client):
    missing, used, issues = [], set(), set()
    for account in client.accounts:
        statements = [doc for doc in usable if doc.account == account]
        gaps = find_gaps(statements, client)
        if gaps:
            missing.append({"cel": cel_id, "account": account, "gaps": gaps})
        used |= {doc.document_id for doc in statements}
        issues |= balance_breaks(account, statements)
    return missing, used, issues


def check_requirement(requirement, docs, library, client, as_of):
    cel_id = requirement["cel"]
    cel = library.cels[cel_id]
    usable = [doc for doc in docs if is_usable(doc, requirement, cel, client, as_of)]
    used = {doc.document_id for doc in usable}
    coverage = requirement.get("coverage")
    if coverage is None:
        missing = [] if usable else [{"cel": cel_id}]
        return missing, used, set()
    if coverage == "engagement_period":
        gaps = find_gaps(usable, client)
        missing = [{"cel": cel_id, "gaps": gaps}] if gaps else []
        return missing, used, set()
    return check_each_account(cel_id, usable, client)


def pending_document_issues(pda, docs):
    cels = {requirement["cel"] for requirement in all_requirements(pda)}
    return {f"document:{doc.document_id}" for doc in docs if doc.status == "pending_review" and doc.cel in cels}


def evaluate_pda(pda, docs, library, client, as_of):
    pathways, issues, satisfied_by = [], set(), []
    for pathway in pda["pathways"]:
        missing, used = [], set()
        for requirement in pathway["requirements"]:
            requirement_missing, requirement_used, requirement_issues = check_requirement(
                requirement, docs, library, client, as_of)
            missing += requirement_missing
            used |= requirement_used
            issues |= requirement_issues
        is_first_met_pathway = not missing and not any(earlier["met"] for earlier in pathways)
        if is_first_met_pathway:
            satisfied_by = sorted(used)
        pathways.append({"name": pathway["name"], "met": not missing, "missing": missing})
    issues |= pending_document_issues(pda, docs)
    return {
        "id": pda["id"],
        "name": pda["name"],
        "depends_on": pda.get("depends_on", []),
        "pathways": pathways,
        "satisfied_by": satisfied_by,
        "issues": sorted(issues),
    }


def pda_status(result, pda, results):
    if not any(pathway["met"] for pathway in result["pathways"]):
        return "MISSING"
    if result["issues"]:
        return "PENDING_REVIEW"
    if any(results[dependency]["status"] not in DONE for dependency in result["depends_on"]):
        return "BLOCKED_BY_DEPENDENCY"
    if pda.get("requires_cpa"):
        return "READY_FOR_DETERMINATION"
    return "SATISFIED"


def service_status(security_holds, results):
    if security_holds:
        return "BLOCKED"
    if all(result["status"] in DONE for result in results):
        return "READY"
    return "NOT_READY"


def evaluate_readiness(docs, library, client, as_of):
    service = client.service
    pdas = {pda["id"]: pda for pda in service["pdas"]}
    results = {pda_id: evaluate_pda(pda, docs, library, client, as_of) for pda_id, pda in pdas.items()}
    for pda_id in in_dependency_order(service["pdas"]):
        results[pda_id]["status"] = pda_status(results[pda_id], pdas[pda_id], results)
    security_holds = sorted(f"document:{doc.document_id}" for doc in docs if doc.status == "quarantined")
    return {
        "client_id": client.id,
        "service": service["id"],
        "as_of": as_of.isoformat(),
        "library_version": library.version,
        "status": service_status(security_holds, results.values()),
        "security_holds": security_holds,
        "pdas": [results[pda_id] for pda_id in pdas],
    }
