
import re
import unicodedata


def normalize_name(value: str) -> list:
    """Normalize names for identity comparison."""

    value = unicodedata.normalize("NFKC", value).casefold().strip()

    # Handle Last, First Middle format.
    if value.count(",") == 1:
        last, first = value.split(",", 1)
        value = first + " " + last

    # Replace every non-letter character with a space.
    value = "".join(
        char if char.isalpha() else " "
        for char in value
    )

    # Drop one-letter tokens.
    return [
        token
        for token in value.split()
        if len(token) > 1
    ]


def check_ssn(
    raw_text: str,
    document_type: dict,
    client: dict,
    extracted_fields: dict
) -> bool:
    """
    Compare document SSN with client SSN.

    Full SSNs are compared only in memory.
    They are never returned or persisted.
    """

    identity = document_type.get("identity", {})
    field_name = identity.get("ssn_field")

    if not field_name or field_name not in extracted_fields:
        return False

    pattern = document_type["fields"][field_name]["pattern"]
    match = re.search(
        pattern,
        raw_text,
        re.MULTILINE | re.IGNORECASE
    )

    if not match:
        return False

    document_ssn = match.group(1).strip()
    client_ssn = client.get("ssn", "")

    document_digits = re.sub(r"\D", "", document_ssn)
    client_digits = re.sub(r"\D", "", client_ssn)

    if len(client_digits) != 9:
        return True

    # A masked SSN can only be compared by its last four digits.
    if document_ssn.upper().startswith(("XXX", "***")):
        return document_digits[-4:] != client_digits[-4:]

    # A full SSN must match completely.
    if len(document_digits) == 9:
        return document_digits != client_digits

    return True


def check_name(
    document_type: dict,
    extracted_fields: dict,
    client: dict
) -> bool:
    """Return True when document and client names differ."""

    field_name = document_type.get("identity", {}).get("name_field")

    if not field_name or field_name not in extracted_fields:
        return False

    document_name = normalize_name(extracted_fields[field_name])
    client_name = normalize_name(client.get("name", ""))

    return document_name != client_name


def check_account(
    document_type: dict,
    extracted_fields: dict,
    client: dict
) -> bool:
    """Check whether an extracted account belongs to the client."""

    field_name = document_type.get("identity", {}).get("account_field")

    if not field_name or field_name not in extracted_fields:
        return False

    client_accounts = {
        str(account)[-4:]
        for account in client.get("accounts", [])
    }

    return extracted_fields[field_name] not in client_accounts


def evaluate_governance(
    raw_text: str,
    document_type: dict,
    extracted_fields: dict,
    extraction_flags: list,
    client: dict,
    channel: str,
    policy: dict
) -> dict:
    """
    Evaluate identity, provenance, grade and document status.

    The returned fields are safe for persistence.
    """

    flags = list(extraction_flags)
    identity_mismatch = False

    identity = document_type.get("identity", {})

    # 1. SSN identity check.
    if identity.get("ssn_field"):
        if check_ssn(raw_text, document_type, client, extracted_fields):
            identity_mismatch = True

    # Identity mismatch quarantines the document immediately.
    if identity_mismatch:
        flags.append("identity_mismatch")

        return {
            "fields": {},
            "flags": list(dict.fromkeys(flags)),
            "provenance": None,
            "grade": "E5",
            "status": "quarantined",
            "security_incident": True
        }

    # 2. Name identity check.
    if check_name(document_type, extracted_fields, client):
        flags.append("name_mismatch")

    # 3. Account identity check.
    if check_account(document_type, extracted_fields, client):
        flags.append("unknown_account")

    # 4. Determine provenance from configuration and channel.
    provenance_config = document_type.get("provenance", {})

    by_channel = provenance_config.get("by_channel", {})

    provenance = by_channel.get(
        channel,
        provenance_config.get("default")
    )

    if (
        provenance_config.get("claims_official") is True
        and channel not in by_channel
    ):
        flags.append("claimed_official_source_unverified")

    # 5. Determine grade.
    if any(flag in policy["e4_flags"] for flag in flags):
        grade = "E4"
    elif provenance in ("P1", "P2"):
        grade = "E1"
    else:
        grade = "E3"

    # 6. Determine status.
    if any(flag in policy["review_flags"] for flag in flags):
        status = "pending_review"
    else:
        status = "accepted"

    return {
        "fields": extracted_fields,
        "flags": list(dict.fromkeys(flags)),
        "provenance": provenance,
        "grade": grade,
        "status": status,
        "security_incident": False
    }
