"""Rule-based classification (TASK §4.4). Zero or several matches are never resolved by guessing."""
from __future__ import annotations

from ges_intake.config import DocType, Policy

NO_MATCH = "no_match"
AMBIGUOUS = "ambiguous_type"
SUSPICIOUS = "suspicious_instructions"


def classify(view: str, doc_types: tuple[DocType, ...]) -> tuple[DocType | None, str | None]:
    """The single matching type, or (None, flag) when there is no match or more than one."""
    matches = [dt for dt in doc_types
               if all(p in view for p in dt.match_all) and not any(p in view for p in dt.match_none)]
    if len(matches) == 1:
        return matches[0], None
    return None, NO_MATCH if not matches else AMBIGUOUS


def has_suspicious_instructions(view: str, policy: Policy) -> bool:
    return any(pattern.search(view) for pattern in policy.suspicious_patterns)
