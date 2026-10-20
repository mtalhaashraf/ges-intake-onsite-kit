import re
from datetime import date
from decimal import Decimal, InvalidOperation


def _parse_date(raw):
    """Parse a date string in one of three formats. Returns YYYY-MM-DD or None."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return date(*__import__("time").strptime(raw, fmt)[:3]).isoformat()
        except ValueError:
            continue
    return None


def _parse_money(raw):
    """Parse a money string to Decimal with 2 decimal places. Returns string or None."""
    raw = raw.strip()
    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative = True
        raw = raw[1:-1].strip()
    if raw.startswith("-"):
        negative = True
        raw = raw[1:].strip()
    raw = raw.replace("$", "").replace(",", "").strip()
    if not raw:
        return None
    try:
        d = Decimal(raw)
        if negative:
            d = -d
        return str(d.quantize(Decimal("0.01")))
    except InvalidOperation:
        return None


def _parse_ssn(raw):
    """Parse and mask an SSN. Returns ***-**-XXXX or None."""
    raw = raw.strip()
    # Full SSN: 123-45-6789
    m = re.fullmatch(r"(\d{3})-(\d{2})-(\d{4})", raw)
    if m:
        return f"***-**-{m.group(3)}"
    # Already masked: XXX-XX-1234 or ***-**-1234
    m = re.fullmatch(r"[X*]{3}-[X*]{2}-(\d{4})", raw)
    if m:
        return f"***-**-{m.group(1)}"
    return None


def _parse_ein(raw):
    """Parse and mask an EIN. Returns **-***XXXX or None."""
    raw = raw.strip()
    m = re.fullmatch(r"(\d{2})-(\d{7})", raw)
    if m:
        last4 = m.group(2)[-4:]
        return f"**-***{last4}"
    return None


def _parse_account(raw):
    """Parse an account number, return last 4 digits only."""
    raw = raw.strip()
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) < 4:
        return None
    return digits[-4:]


def _parse_year(raw):
    """Parse a 4-digit year. Returns int or None."""
    raw = raw.strip()
    m = re.fullmatch(r"\d{4}", raw)
    if m:
        return int(raw)
    return None


def _parse_text(raw):
    """Parse a text field. Returns non-empty stripped string or None."""
    raw = raw.strip()
    return raw if raw else None


def extract_fields(norm_text, doc_type_config):
    """Extract all fields defined in a document type config.

    Returns (fields_dict, flags_list).
    fields_dict has the parsed (and masked) values.
    flags_list has any issues found during extraction.
    """
    fields_config = doc_type_config.get("fields", {})
    required = set(doc_type_config.get("required_fields", []))
    flags = []
    fields = {}

    for field_name, field_def in fields_config.items():
        pattern = field_def["pattern"]
        field_type = field_def["type"]

        m = re.search(pattern, norm_text, re.MULTILINE | re.IGNORECASE)
        if not m:
            # Pattern didn't match — field is absent
            if field_type == "date_range":
                # date_range produces two fields
                if field_name + "_start" not in fields:
                    fields[field_name + "_start"] = None
                if field_name + "_end" not in fields:
                    fields[field_name + "_end"] = None
            else:
                fields[field_name] = None
            continue

        raw_value = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else ""

        if field_type == "text":
            parsed = _parse_text(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "date":
            parsed = _parse_date(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "date_range":
            raw_start = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else ""
            raw_end = m.group(2).strip() if m.lastindex and m.lastindex >= 2 else ""
            start = _parse_date(raw_start)
            end = _parse_date(raw_end)
            if start is None or end is None:
                flags.append("unparseable_field")
            elif end < start:
                flags.append("invalid_period")
            fields[field_name + "_start"] = start
            fields[field_name + "_end"] = end

        elif field_type == "money":
            parsed = _parse_money(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "year":
            parsed = _parse_year(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "ssn":
            parsed = _parse_ssn(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "ein":
            parsed = _parse_ein(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

        elif field_type == "account":
            parsed = _parse_account(raw_value)
            if parsed is None:
                flags.append("unparseable_field")
            fields[field_name] = parsed

    # Check required fields
    for req in required:
        # date_range fields expand to _start and _end
        if req in fields_config and fields_config[req]["type"] == "date_range":
            if fields.get(req + "_start") is None or fields.get(req + "_end") is None:
                flags.append("missing_required_field")
        else:
            if fields.get(req) is None:
                flags.append("missing_required_field")

    return fields, sorted(set(flags))
