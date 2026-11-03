
from datetime import date, timedelta
from decimal import Decimal


def parse_iso_date(value):
    """Convert an ISO date string to a date object."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def get_period(fields):
    """Return a document's period as (start, end)."""
    start = fields.get("period_start")
    end = fields.get("period_end")

    if not start or not end:
        return None

    try:
        start = parse_iso_date(start)
        end = parse_iso_date(end)
    except (TypeError, ValueError):
        return None

    if end < start:
        return None

    return start, end


def merge_periods(periods):
    """Merge overlapping or consecutive date periods."""
    if not periods:
        return []

    periods = sorted(periods)
    merged = [list(periods[0])]

    for start, end in periods[1:]:
        previous = merged[-1]

        if start <= previous[1] + timedelta(days=1):
            previous[1] = max(previous[1], end)
        else:
            merged.append([start, end])

    return [(start, end) for start, end in merged]


def calculate_date_gaps(required_start, required_end, periods):
    """
    Find uncovered dates inside a required period.
    Returned gaps are inclusive date pairs.
    """
    required_start = parse_iso_date(required_start)
    required_end = parse_iso_date(required_end)

    if required_end < required_start:
        raise ValueError("Invalid required period")

    clipped = []

    for start, end in periods:
        start = max(parse_iso_date(start), required_start)
        end = min(parse_iso_date(end), required_end)

        if start <= end:
            clipped.append((start, end))

    merged = merge_periods(clipped)
    gaps = []
    cursor = required_start

    for start, end in merged:
        if cursor < start:
            gaps.append((cursor.isoformat(), (start - timedelta(days=1)).isoformat()))

        cursor = max(cursor, end + timedelta(days=1))

    if cursor <= required_end:
        gaps.append((cursor.isoformat(), required_end.isoformat()))

    return gaps


def is_document_usable(
    document,
    requirement,
    cel_by_id,
    engagement,
    as_of
):
    """Check whether a document can satisfy an evidence requirement."""
    if document.get("status") != "accepted":
        return False

    if document.get("cel") != requirement.get("cel"):
        return False

    if document.get("grade") not in requirement.get("accepted_grades", []):
        return False

    cel = cel_by_id.get(requirement["cel"])

    if not cel:
        return False

    provenance = document.get("provenance")

    if provenance not in cel.get("allowed_provenance", []):
        return False

    required_provenance = requirement.get("provenance")

    if required_provenance and provenance not in required_provenance:
        return False

    fields = document.get("fields", {})
    currentness = cel.get("currentness", {})
    rule = currentness.get("rule")

    if rule == "none":
        return True

    if rule == "not_expired":
        expiration = fields.get("expiration")

        if not expiration:
            return False

        try:
            return parse_iso_date(expiration) >= as_of
        except (TypeError, ValueError):
            return False

    if rule == "tax_year":
        tax_year = fields.get("tax_year")
        expected_year = (
            parse_iso_date(engagement["period_end"]).year
            + currentness.get("offset", 0)
        )

        try:
            return int(tax_year) == expected_year
        except (TypeError, ValueError):
            return False

    if rule == "period_overlap":
        period = get_period(fields)

        if not period:
            return False

        engagement_start = parse_iso_date(engagement["period_start"])
        engagement_end = parse_iso_date(engagement["period_end"])

        document_start, document_end = period

        return (
            document_start <= engagement_end
            and document_end >= engagement_start
        )

    return False


def evaluate_requirement(
    requirement,
    documents,
    cel_by_id,
    client,
    engagement
):
    """Evaluate one requirement and return its usable evidence."""
    cel_id = requirement["cel"]

    usable = [
        document
        for document in documents
        if is_document_usable(
            document,
            requirement,
            cel_by_id,
            engagement,
            client.get("_as_of")
        )
    ]

    coverage = requirement.get("coverage")

    if coverage is None:
        return {
            "met": bool(usable),
            "usable_documents": usable,
            "missing": [] if usable else [{"cel": cel_id}]
        }

    required_start = engagement["period_start"]
    required_end = engagement["period_end"]

    if coverage == "engagement_period":
        periods = [
            get_period(document.get("fields", {}))
            for document in usable
        ]
        periods = [period for period in periods if period]

        gaps = calculate_date_gaps(
            required_start,
            required_end,
            periods
        )

        return {
            "met": not gaps,
            "usable_documents": usable,
            "missing": [] if not gaps else [
                {"cel": cel_id, "gaps": gaps}
            ]
        }

    if coverage == "engagement_period_per_account":
        missing = []
        satisfied_documents = []

        for account in client.get("accounts", []):
            account_documents = [
                document
                for document in usable
                if document.get("fields", {}).get("account") == account
            ]

            periods = [
                get_period(document.get("fields", {}))
                for document in account_documents
            ]
            periods = [period for period in periods if period]

            gaps = calculate_date_gaps(
                required_start,
                required_end,
                periods
            )

            if gaps:
                missing.append({
                    "cel": cel_id,
                    "account": account,
                    "gaps": gaps
                })
            else:
                satisfied_documents.extend(account_documents)

        return {
            "met": not missing,
            "usable_documents": satisfied_documents,
            "missing": missing
        }

    raise ValueError(f"Unsupported coverage rule: {coverage}")


def check_balance_continuity(documents, client):
    """
    Check balances between consecutive bank statements.
    Only adjacent statement periods are compared.
    """
    issues = []

    for account in client.get("accounts", []):
        statements = []

        for document in documents:
            fields = document.get("fields", {})

            if (
                document.get("status") != "accepted"
                or document.get("cel") != "CEL-BNK-001"
                or fields.get("account") != account
            ):
                continue

            period = get_period(fields)

            if period:
                statements.append((period[0], period[1], document))

        statements.sort(
            key=lambda item: (
                item[0],
                item[1],
                item[2].get("document_id", "")
            )
        )

        for previous, current in zip(statements, statements[1:]):
            previous_start, previous_end, previous_doc = previous
            current_start, current_end, current_doc = current

            if current_start != previous_end + timedelta(days=1):
                continue

            previous_balance = previous_doc.get("fields", {}).get(
                "ending_balance"
            )
            current_balance = current_doc.get("fields", {}).get(
                "beginning_balance"
            )

            if previous_balance is None or current_balance is None:
                continue

            if Decimal(str(previous_balance)) != Decimal(str(current_balance)):
                issues.append(
                    f"balance_discontinuity:{account}:{current_start.isoformat()}"
                )

    return sorted(set(issues))


def evaluate_pda(
    pda,
    documents,
    cel_by_id,
    client,
    engagement,
    pda_statuses,
    service_pdas
):
    """Evaluate all pathways and determine one PDA's status."""
    pathway_results = []
    met_pathways = []

    for pathway in pda.get("pathways", []):
        requirement_results = [
            evaluate_requirement(
                requirement,
                documents,
                cel_by_id,
                client,
                engagement
            )
            for requirement in pathway.get("requirements", [])
        ]

        met = all(result["met"] for result in requirement_results)

        satisfied_documents = []

        for result in requirement_results:
            satisfied_documents.extend(result["usable_documents"])

        pathway_result = {
            "name": pathway["name"],
            "met": met,
            "missing": [
                item
                for result in requirement_results
                for item in result["missing"]
            ],
            "satisfied_by": sorted({
                document["document_id"]
                for document in satisfied_documents
            })
        }

        pathway_results.append(pathway_result)

        if met:
            met_pathways.append(pathway_result)

    issues = []

    # Balance continuity applies to bank statement requirements.
    has_bank_requirement = any(
        requirement.get("cel") == "CEL-BNK-001"
        for pathway in pda.get("pathways", [])
        for requirement in pathway.get("requirements", [])
    )

    if has_bank_requirement:
        issues.extend(check_balance_continuity(documents, client))

    # Pending-review documents block a PDA if their CEL is relevant.
    relevant_cels = {
        requirement["cel"]
        for pathway in pda.get("pathways", [])
        for requirement in pathway.get("requirements", [])
    }

    for document in documents:
        if (
            document.get("status") == "pending_review"
            and document.get("cel") in relevant_cels
        ):
            issues.append(f"document:{document['document_id']}")

    issues = sorted(set(issues))

    if not met_pathways:
        status = "MISSING"
    elif issues:
        status = "PENDING_REVIEW"
    else:
        dependencies_ready = all(
            pda_statuses.get(dependency) in (
                "SATISFIED",
                "READY_FOR_DETERMINATION"
            )
            for dependency in pda.get("depends_on", [])
        )

        if not dependencies_ready:
            status = "BLOCKED_BY_DEPENDENCY"
        elif pda.get("requires_cpa"):
            status = "READY_FOR_DETERMINATION"
        else:
            status = "SATISFIED"

    satisfied_by = []

    if met_pathways:
        satisfied_by = met_pathways[0]["satisfied_by"]

    return {
        "id": pda["id"],
        "name": pda["name"],
        "status": status,
        "pathways": pathway_results,
        "satisfied_by": sorted(set(satisfied_by)),
        "issues": issues
    }


def evaluate_service(
    service,
    documents,
    cel_by_id,
    client,
    as_of
):
    """Evaluate every PDA and determine overall service readiness."""
    engagement = client["engagement"]

    # Keep the explicit as-of date; never use the system clock.
    evaluation_client = {
        **client,
        "_as_of": as_of
    }

    pda_statuses = {}
    pda_results = []

    for pda in service.get("pdas", []):
        result = evaluate_pda(
            pda,
            documents,
            cel_by_id,
            evaluation_client,
            engagement,
            pda_statuses,
            service.get("pdas", [])
        )

        pda_statuses[pda["id"]] = result["status"]
        pda_results.append(result)

    security_holds = sorted(
        f"document:{document['document_id']}"
        for document in documents
        if document.get("status") == "quarantined"
    )

    if security_holds:
        status = "BLOCKED"
    elif all(
        result["status"] in (
            "SATISFIED",
            "READY_FOR_DETERMINATION"
        )
        for result in pda_results
    ):
        status = "READY"
    else:
        status = "NOT_READY"

    return {
        "service_id": service["id"],
        "service_name": service["name"],
        "status": status,
        "security_holds": security_holds,
        "pdas": pda_results
    }


def evaluate_readiness(
    documents,
    client,
    cel_config,
    service_config,
    as_of
):
    """Public entry point for readiness evaluation."""
    as_of = parse_iso_date(as_of)

    cel_by_id = {
        item["id"]: item
        for item in cel_config["cel"]
    }

    service = next(
        (
            item
            for item in service_config["services"]
            if item["id"] == client["engagement"]["service"]
        ),
        None
    )

    if service is None:
        raise ValueError("Client service not found in services configuration")

    return evaluate_service(
        service,
        documents,
        cel_by_id,
        client,
        as_of
    )
