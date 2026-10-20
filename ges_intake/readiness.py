from datetime import date, timedelta
from decimal import Decimal


def _parse_date(s):
    """Parse YYYY-MM-DD string to date object."""
    return date.fromisoformat(s)


def _date_range_days(start, end):
    """Generate every date from start to end inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _merge_gaps(missing_days):
    """Convert a sorted set of dates into merged inclusive [start, end] gap pairs."""
    if not missing_days:
        return []
    days = sorted(missing_days)
    gaps = []
    gap_start = days[0]
    prev = days[0]
    for d in days[1:]:
        if d == prev + timedelta(days=1):
            prev = d
        else:
            gaps.append([gap_start.isoformat(), prev.isoformat()])
            gap_start = d
            prev = d
    gaps.append([gap_start.isoformat(), prev.isoformat()])
    return gaps


def is_current(doc_fields, cel_config, as_of, engagement):
    """Check if a document is current under its CEL's currentness rule."""
    rule_config = cel_config.get("currentness", {})
    rule = rule_config.get("rule", "none")

    if rule == "none":
        return True

    if rule == "not_expired":
        exp = doc_fields.get("expiration")
        if not exp:
            return False
        return _parse_date(exp) >= as_of

    if rule == "tax_year":
        doc_year = doc_fields.get("tax_year")
        if doc_year is None:
            return False
        offset = rule_config.get("offset", 0)
        expected_year = _parse_date(engagement["period_end"]).year + offset
        return doc_year == expected_year

    if rule == "period_overlap":
        ps = doc_fields.get("period_start")
        pe = doc_fields.get("period_end")
        if not ps or not pe:
            return False
        doc_start = _parse_date(ps)
        doc_end = _parse_date(pe)
        eng_start = _parse_date(engagement["period_start"])
        eng_end = _parse_date(engagement["period_end"])
        return doc_start <= eng_end and doc_end >= eng_start

    return False


def _find_usable_docs(requirement, all_docs, cel_lookup, as_of, engagement):
    """Find all documents usable for a specific requirement."""
    req_cel = requirement["cel"]
    accepted_grades = set(requirement.get("accepted_grades", []))
    req_provenance = set(requirement.get("provenance", []))

    cel_config = cel_lookup.get(req_cel, {})
    allowed_prov = set(cel_config.get("allowed_provenance", []))

    usable = []
    for doc in all_docs:
        if doc["status"] != "accepted":
            continue
        if doc.get("cel") != req_cel:
            continue
        if doc.get("grade") not in accepted_grades:
            continue
        prov = doc.get("provenance")
        if prov not in allowed_prov:
            continue
        if req_provenance and prov not in req_provenance:
            continue
        fields = doc.get("_fields", {})
        if not is_current(fields, cel_config, as_of, engagement):
            continue
        usable.append(doc)
    return usable


def _check_coverage(requirement, usable_docs, engagement, client):
    """Check if usable documents satisfy a requirement's coverage.

    Returns (is_met, missing_list, satisfied_doc_ids).
    """
    coverage = requirement.get("coverage")
    req_cel = requirement["cel"]

    if coverage is None:
        # No coverage: just need at least one usable doc
        if usable_docs:
            return (True, [], [d["document_id"] for d in usable_docs])
        return (False, [{"cel": req_cel}], [])

    eng_start = _parse_date(engagement["period_start"])
    eng_end = _parse_date(engagement["period_end"])
    all_days = set(_date_range_days(eng_start, eng_end))

    if coverage == "engagement_period":
        covered = set()
        for doc in usable_docs:
            fields = doc.get("_fields", {})
            ps = fields.get("period_start")
            pe = fields.get("period_end")
            if ps and pe:
                covered.update(_date_range_days(_parse_date(ps), _parse_date(pe)))
        missing = all_days - covered
        if not missing:
            return (True, [], [d["document_id"] for d in usable_docs])
        gaps = _merge_gaps(missing)
        return (False, [{"cel": req_cel, "gaps": gaps}], [])

    if coverage == "engagement_period_per_account":
        accounts = client.get("accounts", [])
        all_missing = []
        all_satisfied_ids = []
        all_met = True

        for account in accounts:
            account_docs = [d for d in usable_docs
                           if d.get("_fields", {}).get("account") == account]
            covered = set()
            for doc in account_docs:
                fields = doc.get("_fields", {})
                ps = fields.get("period_start")
                pe = fields.get("period_end")
                if ps and pe:
                    covered.update(_date_range_days(_parse_date(ps), _parse_date(pe)))

            missing = all_days - covered
            if missing:
                all_met = False
                gaps = _merge_gaps(missing)
                all_missing.append({"cel": req_cel, "account": account, "gaps": gaps})
            all_satisfied_ids.extend(d["document_id"] for d in account_docs)

        if all_met:
            return (True, [], sorted(set(all_satisfied_ids)))
        return (False, all_missing, [])

    # Unknown coverage type
    return (False, [{"cel": req_cel}], [])


def _check_balance_continuity(usable_docs, client):
    """Check balance continuity for per-account coverage (Tier 3).

    For each account, sort usable statements by (period_start, period_end, document_id).
    For consecutive pairs where b.period_start is exactly the day after a.period_end,
    check that b.beginning_balance == a.ending_balance (as Decimals).
    """
    issues = []
    accounts = client.get("accounts", [])

    for account in accounts:
        account_docs = [d for d in usable_docs
                       if d.get("_fields", {}).get("account") == account]
        account_docs.sort(key=lambda d: (
            d.get("_fields", {}).get("period_start", ""),
            d.get("_fields", {}).get("period_end", ""),
            d["document_id"],
        ))

        for i in range(len(account_docs) - 1):
            a = account_docs[i]
            b = account_docs[i + 1]
            a_end = a.get("_fields", {}).get("period_end")
            b_start = b.get("_fields", {}).get("period_start")
            if not a_end or not b_start:
                continue
            a_end_date = _parse_date(a_end)
            b_start_date = _parse_date(b_start)
            if b_start_date != a_end_date + timedelta(days=1):
                continue
            a_ending = a.get("_fields", {}).get("ending_balance")
            b_beginning = b.get("_fields", {}).get("beginning_balance")
            if a_ending is None or b_beginning is None:
                continue
            if Decimal(a_ending) != Decimal(b_beginning):
                issues.append(f"balance_discontinuity:{account}:{b_start}")

    return issues


def _get_pda_issues(pda, all_docs, balance_issues=None):
    """Find issues for a PDA: pending_review docs + balance discontinuities."""
    req_cels = set()
    for pathway in pda.get("pathways", []):
        for req in pathway.get("requirements", []):
            req_cels.add(req["cel"])

    issues = []
    for doc in all_docs:
        if doc["status"] == "pending_review" and doc.get("cel") in req_cels:
            issues.append(f"document:{doc['document_id']}")
    if balance_issues:
        issues.extend(balance_issues)
    return sorted(set(issues))


def evaluate_readiness(all_docs, client, service, cel_lookup, as_of):
    """Evaluate readiness for a client's engaged service.

    Returns the readiness dict matching the output contract.
    """
    engagement = client["engagement"]
    as_of_date = as_of if isinstance(as_of, date) else _parse_date(as_of)

    # Security holds: quarantined documents
    security_holds = sorted(
        f"document:{d['document_id']}" for d in all_docs if d["status"] == "quarantined"
    )

    pda_results = []
    pda_statuses = {}

    for pda in service["pdas"]:
        pda_id = pda["id"]
        pathways_results = []
        first_met_pathway_ids = []
        any_met = False

        for pathway in pda.get("pathways", []):
            pathway_met = True
            pathway_missing = []
            pathway_satisfied_ids = []

            for req in pathway.get("requirements", []):
                usable = _find_usable_docs(req, all_docs, cel_lookup, as_of_date, engagement)
                met, missing, sat_ids = _check_coverage(req, usable, engagement, client)
                if not met:
                    pathway_met = False
                    pathway_missing.extend(missing)
                else:
                    pathway_satisfied_ids.extend(sat_ids)

            pathways_results.append({
                "name": pathway["name"],
                "met": pathway_met,
                "missing": pathway_missing,
            })

            if pathway_met and not any_met:
                any_met = True
                first_met_pathway_ids = sorted(set(pathway_satisfied_ids))

        # Balance continuity for per-account coverage requirements
        balance_issues = []
        for pathway in pda.get("pathways", []):
            for req in pathway.get("requirements", []):
                if req.get("coverage") == "engagement_period_per_account":
                    usable = _find_usable_docs(req, all_docs, cel_lookup, as_of_date, engagement)
                    balance_issues.extend(_check_balance_continuity(usable, client))

        issues = _get_pda_issues(pda, all_docs, balance_issues)

        # PDA status (first rule that applies)
        if not any_met:
            pda_status = "MISSING"
        elif issues:
            pda_status = "PENDING_REVIEW"
        else:
            # Check dependencies later (need all PDA statuses first)
            pda_status = "_CHECK_DEPS"

        pda_results.append({
            "id": pda_id,
            "name": pda["name"],
            "status": pda_status,
            "depends_on": pda.get("depends_on", []),
            "pathways": pathways_results,
            "satisfied_by": first_met_pathway_ids if any_met else [],
            "issues": issues,
            "_requires_cpa": pda.get("requires_cpa", False),
            "_any_met": any_met,
        })
        pda_statuses[pda_id] = pda_status

    # Resolve dependencies (may need multiple passes for chains)
    changed = True
    while changed:
        changed = False
        for pda_r in pda_results:
            if pda_r["status"] != "_CHECK_DEPS":
                continue
            deps = pda_r["depends_on"]
            deps_ok = all(
                pda_statuses.get(dep) in ("SATISFIED", "READY_FOR_DETERMINATION")
                for dep in deps
            )
            if not deps_ok:
                pda_r["status"] = "BLOCKED_BY_DEPENDENCY"
                if pda_statuses[pda_r["id"]] != "BLOCKED_BY_DEPENDENCY":
                    pda_statuses[pda_r["id"]] = "BLOCKED_BY_DEPENDENCY"
                    changed = True
            else:
                if pda_r["_requires_cpa"]:
                    pda_r["status"] = "READY_FOR_DETERMINATION"
                else:
                    pda_r["status"] = "SATISFIED"
                if pda_statuses[pda_r["id"]] != pda_r["status"]:
                    pda_statuses[pda_r["id"]] = pda_r["status"]
                    changed = True

    # Clean up internal keys
    for pda_r in pda_results:
        del pda_r["_requires_cpa"]
        del pda_r["_any_met"]

    # Service status
    if security_holds:
        service_status = "BLOCKED"
    elif all(pda_statuses[p["id"]] in ("SATISFIED", "READY_FOR_DETERMINATION")
             for p in service["pdas"]):
        service_status = "READY"
    else:
        service_status = "NOT_READY"

    return {
        "client_id": client["client_id"],
        "service": service["id"],
        "as_of": as_of_date.isoformat(),
        "library_version": "",  # filled by caller
        "status": service_status,
        "security_holds": security_holds,
        "pdas": pda_results,
    }
