import re
import unicodedata


def _normalise_name_tokens(name):
    """Normalise a name for comparison: NFKC, casefold, reorder Last/First, drop 1-char tokens."""
    name = unicodedata.normalize("NFKC", name)
    name = name.casefold()
    # If exactly one comma, treat as "Last, First Middle" → "First Middle Last"
    parts = name.split(",")
    if len(parts) == 2:
        name = parts[1].strip() + " " + parts[0].strip()
    # Replace every non-letter with a space
    name = re.sub(r"[^a-z]", " ", name)
    tokens = name.split()
    # Drop one-letter tokens (initials like "Q")
    tokens = [t for t in tokens if len(t) > 1]
    return tokens


def check_identity(fields, doc_type_config, client):
    """Run identity checks: SSN, name, account.

    Returns (flags, should_quarantine, cleared_fields).
    If should_quarantine is True, all extracted fields must be wiped.
    """
    identity = doc_type_config.get("identity", {})
    flags = []

    # --- 1. SSN check ---
    ssn_field = identity.get("ssn_field")
    if ssn_field and fields.get(ssn_field):
        doc_ssn_last4 = fields[ssn_field].split("-")[-1]
        client_ssn_last4 = client["ssn"].split("-")[-1]
        if doc_ssn_last4 != client_ssn_last4:
            flags.append("identity_mismatch")
            return (flags, True, {})

    # --- 2. Name check ---
    name_field = identity.get("name_field")
    if name_field and fields.get(name_field):
        doc_tokens = _normalise_name_tokens(fields[name_field])
        client_tokens = _normalise_name_tokens(client["name"])
        if doc_tokens != client_tokens:
            flags.append("name_mismatch")

    # --- 3. Account check ---
    account_field = identity.get("account_field")
    if account_field and fields.get(account_field):
        doc_account = fields[account_field]
        if doc_account not in client.get("accounts", []):
            flags.append("unknown_account")

    return (flags, False, fields)


def determine_provenance(doc_type_config, channel):
    """Determine provenance from channel, never from document text.

    Returns (provenance_code, extra_flags).
    """
    prov_config = doc_type_config.get("provenance", {})
    flags = []

    by_channel = prov_config.get("by_channel", {})
    if channel in by_channel:
        provenance = by_channel[channel]
    else:
        provenance = prov_config.get("default", "P3")
        if prov_config.get("claims_official"):
            flags.append("claimed_official_source_unverified")

    return (provenance, flags)


def determine_grade(provenance, all_flags, e4_flags):
    """Compute evidence grade based on provenance and flags."""
    if "identity_mismatch" in all_flags:
        return "E5"
    if any(f in e4_flags for f in all_flags):
        return "E4"
    if provenance in ("P1", "P2"):
        return "E1"
    return "E3"


def determine_status(grade, all_flags, review_flags):
    """Compute document status based on grade and flags."""
    if grade == "E5":
        return "quarantined"
    if any(f in review_flags for f in all_flags):
        return "pending_review"
    return "accepted"
