
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation


DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%B %d, %Y",
)


def parse_date(value: str):
    """Parse only the date formats allowed by the assignment."""
    value = value.strip()

    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value, date_format).date().isoformat()
        except ValueError:
            continue

    raise ValueError("Invalid date")


def parse_money(value: str) -> str:
    """Parse money without using float and return two decimal places."""
    value = value.strip()
    negative = False

    if value.startswith("(") and value.endswith(")"):
        negative = True
        value = value[1:-1].strip()
    elif value.startswith("-"):
        negative = True
        value = value[1:].strip()

    if value.startswith("$"):
        value = value[1:].strip()

    # Reject malformed values rather than silently cleaning them.
    if not re.fullmatch(r"\d+(?:,\d{3})*(?:\.\d+)?", value):
        raise ValueError("Invalid money")

    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation:
        raise ValueError("Invalid money") from None

    if negative:
        amount = -amount

    return f"{amount:.2f}"


def parse_ssn(value: str) -> str:
    """
    Validate a full or already-masked SSN.
    Return only the masked representation.
    """
    value = value.strip()

    full_match = re.fullmatch(r"(\d{3})-(\d{2})-(\d{4})", value)
    masked_match = re.fullmatch(r"(?:XXX|\*\*\*)-(?:XX|\*\*)-(\d{4})", value, re.I)

    if full_match:
        return f"***-**-{full_match.group(3)}"

    if masked_match:
        return f"***-**-{masked_match.group(1)}"

    raise ValueError("Invalid SSN")


def parse_ein(value: str) -> str:
    """
    Validate an EIN and return a masked representation.
    """
    value = value.strip()
    match = re.fullmatch(r"(\d{2})-(\d{7})", value)

    if not match:
        raise ValueError("Invalid EIN")

    digits = match.group(1) + match.group(2)

    # Preserve only the final four digits.
    return f"**-***{digits[-4:]}"


def parse_account(value: str) -> str:
    """Return only the last four digits of an account-like identifier."""
    value = value.strip()

    if not re.fullmatch(r"[\d -]+", value):
        raise ValueError("Invalid account")

    digits = re.sub(r"\D", "", value)

    if len(digits) < 4:
        raise ValueError("Invalid account")

    return digits[-4:]


def parse_field_value(field_type: str, match: re.Match):
    """Convert one regex match according to its configured field type."""

    if field_type == "date_range":
        if match.lastindex != 2:
            raise ValueError("Date range requires two capture groups")

        start = parse_date(match.group(1))
        end = parse_date(match.group(2))

        return {
            "start": start,
            "end": end
        }

    if match.lastindex is None:
        raise ValueError("Field pattern requires a capture group")

    value = match.group(1).strip()

    if field_type == "text":
        if not value:
            raise ValueError("Empty text")
        return value

    if field_type == "date":
        return parse_date(value)

    if field_type == "money":
        return parse_money(value)

    if field_type == "year":
        if not re.fullmatch(r"\d{4}", value):
            raise ValueError("Invalid year")
        return int(value)

    if field_type == "ssn":
        return parse_ssn(value)

    if field_type == "ein":
        return parse_ein(value)

    if field_type == "account":
        return parse_account(value)

    raise ValueError(f"Unsupported field type: {field_type}")


def extract_fields(text: str, document_type: dict) -> dict:
    """
    Extract configured fields from normalized document text.

    Sensitive identifiers are converted to masked/last-four forms
    before they are returned.
    """
    fields = {}
    flags = []

    for field_name, config in document_type.get("fields", {}).items():
        pattern = config["pattern"]
        field_type = config["type"]

        match = re.search(
            pattern,
            text,
            re.MULTILINE | re.IGNORECASE
        )

        if match is None:
            continue

        try:
            value = parse_field_value(field_type, match)

            if field_type == "date_range":
                fields[f"{field_name}_start"] = value["start"]
                fields[f"{field_name}_end"] = value["end"]
            else:
                fields[field_name] = value

        except (ValueError, InvalidOperation):
            flags.append("unparseable_field")

    # Required fields are checked after extraction.
    for required_field in document_type.get("required_fields", []):
        if field_type_for_required(document_type, required_field, fields) is False:
            flags.append("missing_required_field")

    # A date range ending before it starts is invalid.
    for field_name, config in document_type.get("fields", {}).items():
        if config["type"] == "date_range":
            start = fields.get(f"{field_name}_start")
            end = fields.get(f"{field_name}_end")

            if start is not None and end is not None and end < start:
                flags.append("invalid_period")

    # Avoid duplicate flags.
    return {
        "fields": fields,
        "flags": list(dict.fromkeys(flags))
    }


def field_type_for_required(document_type: dict, required_field: str, fields: dict) -> bool:
    """Check required fields, including the two outputs of a date range."""
    config = document_type.get("fields", {}).get(required_field)

    if config is None:
        return required_field in fields

    if config.get("type") == "date_range":
        return (
            f"{required_field}_start" in fields
            and f"{required_field}_end" in fields
        )

    return required_field in fields
