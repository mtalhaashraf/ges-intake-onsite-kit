from datetime import date, timedelta

ROLES = ("client", "eic", "cpa", "owner")
LEVEL_ORDER = ["NOW", "NEXT", "WATCH"]
LEVEL_FOR_CRITICALITY = {"C1": "NOW", "C2": "NEXT", "C3": "NEXT"}


def add_item(items, levels, kind, key, staff_message, client_message=None):
    for role, level in levels.items():
        message = client_message if role == "client" else staff_message
        items[role].setdefault(key, {"level": level, "kind": kind, "key": key, "message": message})


def merge_ranges(gaps):
    merged = []
    for start, end in sorted((date.fromisoformat(start), date.fromisoformat(end)) for start, end in gaps):
        touches_previous = merged and start <= merged[-1][1] + timedelta(days=1)
        if touches_previous:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start.isoformat(), end.isoformat()) for start, end in merged]


def describe_dates(gaps):
    ranges = []
    for start, end in merge_ranges(gaps):
        ranges.append(start if start == end else f"{start} to {end}")
    return ", ".join(ranges)


def describe_missing(entries):
    gaps_by_account = {}
    for entry in entries:
        gaps_by_account.setdefault(entry.get("account"), []).extend(entry.get("gaps", []))
    parts = []
    for account, gaps in gaps_by_account.items():
        part = ""
        if account:
            part += f" for the account ending {account}"
        if gaps:
            part += f" covering {describe_dates(gaps)}"
        if part:
            parts.append(part)
    return ";".join(parts)


def missing_evidence_by_cel(readiness):
    by_cel = {}
    for pda in readiness["pdas"]:
        if pda["status"] == "MISSING":
            for entry in pda["pathways"][0]["missing"]:
                by_cel.setdefault(entry["cel"], []).append(entry)
    return by_cel


def add_missing_evidence(items, readiness, library):
    for cel_id, entries in missing_evidence_by_cel(readiness).items():
        cel = library.cels[cel_id]
        level = LEVEL_FOR_CRITICALITY.get(cel.get("criticality"))
        if level is None:
            continue
        details = describe_missing(entries)
        add_item(items, {"client": level, "eic": level}, "missing_evidence", f"missing:{cel_id}",
                 staff_message=f"Follow up with the client for {cel['name']} ({cel_id}){details}.",
                 client_message=f"Please upload {cel['client_label']}{details}.")


def add_document_issues(items, docs):
    for doc in docs:
        key = f"issue:document:{doc.document_id}"
        if doc.status == "pending_review":
            add_item(items, {"eic": "NOW", "cpa": "WATCH"}, "review_required", key,
                     f"{doc.document_id} needs review: {', '.join(sorted(doc.flags))}.")
        elif doc.status == "quarantined":
            add_item(items, {"eic": "NOW", "cpa": "WATCH", "owner": "NOW"}, "security_incident", key,
                     f"{doc.document_id} identifies a different taxpayer and is held; do not use or forward it.")


def add_pda_items(items, readiness):
    for pda in readiness["pdas"]:
        for issue in pda["issues"]:
            if issue.startswith("balance_discontinuity:"):
                _, account, day = issue.split(":")
                add_item(items, {"eic": "NOW", "cpa": "WATCH"}, "review_required", f"issue:{issue}",
                         f"Account ending {account}: the statement starting {day} does not open "
                         f"with the previous statement's ending balance.")
        key = f"pda:{pda['id']}"
        if pda["status"] == "READY_FOR_DETERMINATION":
            add_item(items, {"cpa": "NOW"}, "ready_for_determination", key,
                     f"{pda['id']} {pda['name']}: evidence is ready for your determination.")
        elif pda["status"] == "BLOCKED_BY_DEPENDENCY":
            add_item(items, {"cpa": "WATCH"}, "blocked_by_dependency", key,
                     f"{pda['id']} {pda['name']}: waiting on {', '.join(pda['depends_on'])}.")


def sorted_items(role, items_by_key):
    ordered = sorted(items_by_key.values(),
                     key=lambda item: (LEVEL_ORDER.index(item["level"]), item["kind"], item["key"]))
    if role == "client":
        return [{"level": item["level"], "kind": item["kind"], "message": item["message"]} for item in ordered]
    return ordered


def build_attention(readiness, docs, library):
    items = {role: {} for role in ROLES}
    add_missing_evidence(items, readiness, library)
    add_document_issues(items, docs)
    add_pda_items(items, readiness)
    return {role: sorted_items(role, items[role]) for role in ROLES}
