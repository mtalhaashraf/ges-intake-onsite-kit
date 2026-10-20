"""Typed field parsing and extraction (TASK §4.5).

Identifiers leave this module masked. A full SSN is kept only in `Extraction.ssn_full`, for the
identity check in `govern`, and is never persisted or printed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from ges_intake.text import scrub_identifiers

if TYPE_CHECKING:
    from ges_intake.config import DocType

UNPARSEABLE = "unparseable_field"
MISSING_REQUIRED = "missing_required_field"
INVALID_PERIOD = "invalid_period"

_MONTHS = {name: number for number, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"), start=1)}

# Month names are matched from a fixed English table rather than strptime("%B"), which depends
# on the process locale. All digit classes are ASCII: int() would happily accept Arabic-Indic digits.
_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{1,2})-([0-9]{1,2})")
_STRICT_ISO_DATE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_US_DATE = re.compile(r"([0-9]{1,2})/([0-9]{1,2})/([0-9]{4})")
_LONG_DATE = re.compile(r"([A-Za-z]+)\s+([0-9]{1,2}),\s+([0-9]{4})")
_MONEY = re.compile(
    r"(?P<open>\()?\s*(?P<minus>-)?\s*\$?\s*(?P<minus2>-)?\s*(?P<open2>\()?\s*"
    r"(?P<amount>[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
    r"\s*(?P<close>\))?"
)
_YEAR = re.compile(r"[0-9]{4}")
_SSN_FULL = re.compile(r"[0-9]{3}-[0-9]{2}-([0-9]{4})")
_SSN_MASKED = re.compile(r"(?:[Xx]{3}-[Xx]{2}|\*{3}-\*{2})-([0-9]{4})")
_EIN = re.compile(r"[0-9]{2}-[0-9]{3}([0-9]{4})")
_ACCOUNT = re.compile(r"[0-9 \-]+")
_CENT = Decimal("0.01")
_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Ssn:
    last4: str
    full: str | None = field(default=None, repr=False)  # nine digits, only if the source showed them

    @property
    def masked(self) -> str:
        return f"***-**-{self.last4}"


def parse_date(raw: str) -> date | None:
    """`%Y-%m-%d`, `%m/%d/%Y` or `%B %d, %Y` (US order, English month names)."""
    if m := _ISO_DATE.fullmatch(raw):
        year, month, day = m.groups()
    elif m := _US_DATE.fullmatch(raw):
        month, day, year = m.groups()
    elif m := _LONG_DATE.fullmatch(raw):
        month = _MONTHS.get(m.group(1).lower())
        if month is None:
            return None
        day, year = m.group(2), m.group(3)
    else:
        return None
    try:
        return date(int(year), int(month), int(day))
    except ValueError:
        return None


def parse_iso_date(raw: object) -> date | None:
    """Strict `YYYY-MM-DD`, for configuration files."""
    if not isinstance(raw, str) or not (m := _STRICT_ISO_DATE.fullmatch(raw)):
        return None
    try:
        return date(*(int(part) for part in m.groups()))
    except ValueError:
        return None


def parse_money(raw: str) -> Decimal | None:
    """Optional `$`, thousands commas, `-` or `( )` for negative. Refuses to round sub-cent values."""
    m = _MONEY.fullmatch(raw)
    if m is None:
        return None
    opens = bool(m["open"]) + bool(m["open2"])
    if opens > 1 or opens != bool(m["close"]):
        return None
    if opens + bool(m["minus"]) + bool(m["minus2"]) > 1:
        return None
    value = Decimal(m["amount"].replace(",", ""))
    cents = value.quantize(_CENT)
    if cents != value:
        return None
    if opens or m["minus"] or m["minus2"]:
        cents = -cents
    return cents if cents else _ZERO


def parse_year(raw: str) -> int | None:
    return int(raw) if _YEAR.fullmatch(raw) else None


def parse_ssn(raw: str) -> Ssn | None:
    if m := _SSN_FULL.fullmatch(raw):
        return Ssn(last4=m.group(1), full=raw.replace("-", ""))
    if m := _SSN_MASKED.fullmatch(raw):
        return Ssn(last4=m.group(1))
    return None


def parse_ein(raw: str) -> str | None:
    m = _EIN.fullmatch(raw)
    return f"**-***{m.group(1)}" if m else None


def parse_account(raw: str) -> str | None:
    """Digits, spaces and dashes with at least four digits; keeps only the last four."""
    if not _ACCOUNT.fullmatch(raw):
        return None
    digits = [ch for ch in raw if ch.isdigit()]
    return "".join(digits[-4:]) if len(digits) >= 4 else None


def parse_text(raw: str) -> str | None:
    return scrub_identifiers(raw) if raw else None


_PARSERS = {
    "text": parse_text,
    "date": parse_date,
    "money": parse_money,
    "year": parse_year,
    "ssn": parse_ssn,
    "ein": parse_ein,
    "account": parse_account,
}


@dataclass
class Extraction:
    values: dict[str, object] = field(default_factory=dict)
    ssn_full: dict[str, str] = field(default_factory=dict, repr=False)
    flags: set[str] = field(default_factory=set)


def extract(doc_type: DocType, text: str) -> Extraction:
    """Run every field pattern of `doc_type` over the normalised text."""
    out = Extraction()
    for spec in doc_type.fields:
        match = spec.regex.search(text)
        if match is None:
            continue
        if spec.type == "date_range":
            start, end = (_parse_group(match, group, parse_date) for group in (1, 2))
            if start is None or end is None:
                out.flags.add(UNPARSEABLE)
                continue
            if end < start:
                out.flags.add(INVALID_PERIOD)
            out.values[f"{spec.name}_start"] = start
            out.values[f"{spec.name}_end"] = end
            continue
        parsed = _parse_group(match, 1, _PARSERS[spec.type])
        if parsed is None:
            out.flags.add(UNPARSEABLE)
        elif isinstance(parsed, Ssn):
            out.values[spec.name] = parsed.masked
            if parsed.full:
                out.ssn_full[spec.name] = parsed.full
        else:
            out.values[spec.name] = parsed
    for name in doc_type.required_fields:
        if not all(persisted in out.values for persisted in doc_type.field(name).persisted_names):
            out.flags.add(MISSING_REQUIRED)
    return out


def _parse_group(match: re.Match, group: int, parser):
    raw = match.group(group)
    return None if raw is None else parser(raw.strip())


def to_json(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return value
