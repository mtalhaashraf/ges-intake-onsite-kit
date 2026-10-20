import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y")
MONEY = re.compile(r"(-)?\$?(-)?([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]{1,2})?")
SSN = re.compile(r"([0-9]{3}-[0-9]{2}|XXX-XX|\*\*\*-\*\*)-([0-9]{4})", re.IGNORECASE)
EIN = re.compile(r"[0-9]{2}-[0-9]{7}")
IDENTIFIER_TYPES = {"ssn", "ein", "account"}


@dataclass
class Doc:
    document_id: str
    source: str
    received_at: datetime
    channel: str
    doc_type: str | None = None
    cel: str | None = None
    provenance: str | None = None
    grade: str | None = None
    base_status: str = "pending_review"
    flags: set = field(default_factory=set)
    fields: dict = field(default_factory=dict)
    full_identifiers: dict = field(default_factory=dict)
    account: str | None = None
    advisory: dict | None = None
    superseded: bool = False

    @property
    def status(self):
        return "superseded" if self.superseded else self.base_status

    def report(self):
        report = {
            "source": self.source,
            "document_id": self.document_id,
            "doc_type": self.doc_type,
            "cel": self.cel,
            "provenance": self.provenance,
            "grade": self.grade,
            "status": self.status,
            "flags": sorted(self.flags),
        }
        if self.advisory is not None:
            report["advisory"] = self.advisory
        return report


def normalize(text):
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFKC", text)


def phrase_view(text):
    return re.sub(r"\s+", " ", text.casefold())


def digits_only(text):
    return re.sub(r"[^0-9]", "", text)


def parse_text(text):
    return text or None


def parse_date(text):
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            pass
    return None


def parse_money(text):
    in_parentheses = text.startswith("(") and text.endswith(")")
    if in_parentheses:
        text = text[1:-1].strip()
    match = MONEY.fullmatch(text)
    if not match:
        return None
    minus_signs = in_parentheses + bool(match[1]) + bool(match[2])
    if minus_signs > 1:
        return None
    amount = Decimal(match[3].replace(",", "") + (match[4] or "")).quantize(Decimal("0.01"))
    return -amount if minus_signs else amount


def parse_year(text):
    return int(text) if re.fullmatch(r"[0-9]{4}", text) else None


def parse_ssn(text):
    match = SSN.fullmatch(text)
    return f"***-**-{match[2]}" if match else None


def parse_ein(text):
    return f"**-***{text[-4:]}" if EIN.fullmatch(text) else None


def parse_account(text):
    if re.fullmatch(r"[0-9 -]+", text) and len(digits_only(text)) >= 4:
        return digits_only(text)[-4:]
    return None


PARSERS = {
    "text": parse_text,
    "date": parse_date,
    "money": parse_money,
    "year": parse_year,
    "ssn": parse_ssn,
    "ein": parse_ein,
    "account": parse_account,
}


def captured(match, group_number):
    groups = match.groups()
    if len(groups) < group_number or groups[group_number - 1] is None:
        return ""
    return groups[group_number - 1].strip()


def extract_fields(doc_type, text):
    fields, full_identifiers, problems = {}, {}, set()
    for name, spec in doc_type["fields"].items():
        match = spec["regex"].search(text)
        if not match:
            continue
        if spec["type"] == "date_range":
            start, end = parse_date(captured(match, 1)), parse_date(captured(match, 2))
            if start is None or end is None:
                problems.add("unparseable_field")
                continue
            if end < start:
                problems.add("invalid_period")
            fields[f"{name}_start"], fields[f"{name}_end"] = start, end
            continue
        raw_value = captured(match, 1)
        value = PARSERS[spec["type"]](raw_value)
        if value is None:
            problems.add("unparseable_field")
            continue
        fields[name] = value
        if spec["type"] in IDENTIFIER_TYPES:
            full_identifiers[name] = digits_only(raw_value)
    for required in doc_type.get("required_fields", []):
        if required not in fields and f"{required}_start" not in fields:
            problems.add("missing_required_field")
    return fields, full_identifiers, problems


def name_tokens(name):
    text = unicodedata.normalize("NFKC", name).casefold()
    if text.count(",") == 1:
        last, first_and_middle = text.split(",")
        text = f"{first_and_middle} {last}"
    letters_only = "".join(character if character.isalpha() else " " for character in text)
    return [token for token in letters_only.split() if len(token) > 1]


def same_person(name, other_name):
    return name_tokens(name) == name_tokens(other_name)


def belongs_to_someone_else(ssn_digits, client):
    if ssn_digits[-4:] != client.ssn[-4:]:
        return True
    return len(ssn_digits) == 9 and ssn_digits != client.ssn


def contains_suspicious_instructions(view, library):
    return any(pattern.search(view) for pattern in library.suspicious_patterns)


def matching_types(view, library):
    matches = []
    for doc_type in library.document_types.values():
        has_every_phrase = all(phrase in view for phrase in doc_type["must_contain"])
        has_forbidden_phrase = any(phrase in view for phrase in doc_type["must_not_contain"])
        if has_every_phrase and not has_forbidden_phrase:
            matches.append(doc_type)
    return matches


def provenance_for(doc_type, channel):
    by_channel = doc_type["provenance"].get("by_channel", {})
    return by_channel.get(channel, doc_type["provenance"]["default"])


def claims_unverified_official_source(doc_type, channel):
    by_channel = doc_type["provenance"].get("by_channel", {})
    return bool(doc_type["provenance"].get("claims_official")) and channel not in by_channel


def grade_for(doc, policy):
    if doc.flags & set(policy["e4_flags"]):
        return "E4"
    if doc.provenance in ("P1", "P2"):
        return "E1"
    return "E3"


def status_for(doc, policy):
    if doc.flags & set(policy["review_flags"]):
        return "pending_review"
    return "accepted"


def quarantine(doc):
    doc.flags.add("identity_mismatch")
    doc.provenance = None
    doc.grade = "E5"
    doc.base_status = "quarantined"


def govern_document(doc, raw_text, library, client):
    text = normalize(raw_text)
    view = phrase_view(text)
    if contains_suspicious_instructions(view, library):
        doc.flags.add("suspicious_instructions")

    candidates = matching_types(view, library)
    if len(candidates) == 0:
        doc.flags.add("no_match")
        return doc
    if len(candidates) > 1:
        doc.flags.add("ambiguous_type")
        return doc
    doc_type = candidates[0]
    doc.doc_type, doc.cel = doc_type["id"], doc_type["cel"]

    fields, full_identifiers, problems = extract_fields(doc_type, text)
    doc.flags |= problems
    identity = doc_type.get("identity", {})

    ssn_field = identity.get("ssn_field")
    if ssn_field in fields and belongs_to_someone_else(full_identifiers.get(ssn_field, ""), client):
        quarantine(doc)
        return doc

    name_field = identity.get("name_field")
    if name_field in fields and not same_person(fields[name_field], client.name):
        doc.flags.add("name_mismatch")

    account_field = identity.get("account_field")
    if account_field in fields:
        doc.account = fields[account_field]
        if doc.account not in client.accounts:
            doc.flags.add("unknown_account")

    doc.provenance = provenance_for(doc_type, doc.channel)
    if claims_unverified_official_source(doc_type, doc.channel):
        doc.flags.add("claimed_official_source_unverified")
    doc.grade = grade_for(doc, library.policy)
    doc.base_status = status_for(doc, library.policy)
    doc.fields, doc.full_identifiers = fields, full_identifiers
    return doc
