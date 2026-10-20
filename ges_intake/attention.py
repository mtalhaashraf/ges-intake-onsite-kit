def compute_attention(readiness, all_docs, cel_lookup, service):
    """Compute attention lists for each role.

    Returns {"client": [...], "eic": [...], "cpa": [...], "owner": [...]}.
    """
    client_items = []
    eic_items = []
    cpa_items = []
    owner_items = []

    # Track missing evidence by CEL to deduplicate across PDAs
    seen_missing_cels = set()

    for pda in readiness.get("pdas", []):
        pda_id = pda["id"]
        pda_status = pda["status"]

        # Missing evidence: from the FIRST pathway of each MISSING PDA
        if pda_status == "MISSING" and pda.get("pathways"):
            first_pathway = pda["pathways"][0]
            for missing in first_pathway.get("missing", []):
                cel_id = missing["cel"]
                if cel_id in seen_missing_cels:
                    continue
                seen_missing_cels.add(cel_id)

                cel = cel_lookup.get(cel_id, {})
                criticality = cel.get("criticality", "C4")
                client_label = cel.get("client_label", cel_id)

                if criticality in ("C1",):
                    level = "NOW"
                elif criticality in ("C2", "C3"):
                    level = "NEXT"
                else:
                    continue  # C4: nothing

                key = f"missing:{cel_id}"

                # Build client message (no internal codes)
                gaps = missing.get("gaps", [])
                account = missing.get("account")
                if gaps and account:
                    gap_str = ", ".join(f"{g[0]} to {g[1]}" for g in gaps)
                    client_msg = f"Please upload {client_label} for account ending {account} covering: {gap_str}"
                elif gaps:
                    gap_str = ", ".join(f"{g[0]} to {g[1]}" for g in gaps)
                    client_msg = f"Please upload {client_label} covering: {gap_str}"
                else:
                    client_msg = f"Please upload {client_label}"

                client_items.append({
                    "level": level,
                    "kind": "missing_evidence",
                    "message": client_msg,
                })
                eic_items.append({
                    "level": level,
                    "kind": "missing_evidence",
                    "key": key,
                    "message": f"Missing {cel.get('name', cel_id)} ({cel_id})",
                })

        # Open review issues
        for issue in pda.get("issues", []):
            if issue.startswith("document:"):
                eic_items.append({
                    "level": "NOW",
                    "kind": "review_required",
                    "key": f"issue:{issue}",
                    "message": f"Review required: {issue}",
                })
                cpa_items.append({
                    "level": "WATCH",
                    "kind": "review_required",
                    "key": f"issue:{issue}",
                    "message": f"Review pending: {issue}",
                })
            elif issue.startswith("balance_discontinuity:"):
                eic_items.append({
                    "level": "NOW",
                    "kind": "review_required",
                    "key": f"issue:{issue}",
                    "message": f"Balance discontinuity: {issue}",
                })
                cpa_items.append({
                    "level": "WATCH",
                    "kind": "review_required",
                    "key": f"issue:{issue}",
                    "message": f"Balance discontinuity: {issue}",
                })

        # Quarantined documents
        if pda_status == "MISSING" or pda_status == "PENDING_REVIEW":
            pass  # handled via issues above

        # READY_FOR_DETERMINATION
        if pda_status == "READY_FOR_DETERMINATION":
            cpa_items.append({
                "level": "NOW",
                "kind": "ready_for_determination",
                "key": f"pda:{pda_id}",
                "message": f"PDA {pda_id} ready for determination",
            })

        # BLOCKED_BY_DEPENDENCY
        if pda_status == "BLOCKED_BY_DEPENDENCY":
            cpa_items.append({
                "level": "WATCH",
                "kind": "blocked_by_dependency",
                "key": f"pda:{pda_id}",
                "message": f"PDA {pda_id} blocked by dependency on {pda.get('depends_on', [])}",
            })

    # Quarantined documents → security incidents
    for doc in all_docs:
        if doc["status"] == "quarantined":
            doc_id = doc["document_id"]
            eic_items.append({
                "level": "NOW",
                "kind": "security_incident",
                "key": f"issue:document:{doc_id}",
                "message": f"Security incident: quarantined document {doc_id}",
            })
            cpa_items.append({
                "level": "WATCH",
                "kind": "security_incident",
                "key": f"issue:document:{doc_id}",
                "message": f"Security incident: quarantined document {doc_id}",
            })
            owner_items.append({
                "level": "NOW",
                "kind": "security_incident",
                "key": f"issue:document:{doc_id}",
                "message": f"Security incident: quarantined document {doc_id}",
            })

    # Sort: level (NOW, NEXT, WATCH), then kind, then key
    level_order = {"NOW": 0, "NEXT": 1, "WATCH": 2}

    def sort_key(item):
        return (level_order.get(item["level"], 99), item["kind"], item.get("key", ""))

    # Deduplicate by key per role (except client which has no key)
    def dedup(items):
        seen = set()
        result = []
        for item in sorted(items, key=sort_key):
            k = item.get("key")
            if k and k in seen:
                continue
            if k:
                seen.add(k)
            result.append(item)
        return result

    return {
        "client": sorted(client_items, key=sort_key),
        "eic": dedup(eic_items),
        "cpa": dedup(cpa_items),
        "owner": dedup(owner_items),
    }
