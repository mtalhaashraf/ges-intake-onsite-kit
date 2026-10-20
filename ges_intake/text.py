"""Text normalisation (TASK §4.4), name comparison (§4.6) and identifier scrubbing (§4.5, §4.13)."""
from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")

# Scrubbing is deliberately broad: over-redacting a number costs nothing, leaking one is the
# worst failure the exercise names. \d is Unicode-aware here on purpose.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_SSN_SHAPE = re.compile(r"(?<!\d)\d{3}[\s.\-‐-―]\d{2}[\s.\-‐-―]\d{4}(?!\d)")
_DIGIT_RUN = re.compile(r"\d(?:[ \t\-‐-―]?\d)*")
_ISO_DATE_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MIN_SCRUB_DIGITS = 5


def normalise(text: str) -> str:
    """Strip a leading BOM, unify line endings, apply NFKC."""
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFKC", text)


def phrase_view(normalised: str) -> str:
    """Casefolded text with every run of whitespace collapsed to one space."""
    return _WHITESPACE.sub(" ", normalised.casefold())


def phrase_key(phrase: str) -> str:
    """A library phrase in the same form as the phrase view, so the two can be compared."""
    return phrase_view(unicodedata.normalize("NFKC", phrase))


def name_tokens(name: str) -> list[str]:
    """Comparable tokens of a personal name: "RIVERA, JORDAN Q" -> ["jordan", "rivera"]."""
    folded = unicodedata.normalize("NFKC", name).casefold()
    if folded.count(",") == 1:
        last, rest = folded.split(",")
        folded = f"{rest} {last}"
    letters = "".join(ch if ch.isalpha() else " " for ch in folded)
    return [token for token in letters.split() if len(token) > 1]


def scrub_identifiers(text: str, names: tuple[str, ...] = ()) -> str:
    """Replace anything that could be an SSN, EIN, account, licence or passport number.

    Any run of five or more digits (optionally separated by single spaces or dashes) is
    replaced, except an ISO date. Last-4 forms such as "***-**-1234" survive, as §4.5 allows.
    `names` are extra tokens (e.g. the client's name) to redact as whole words.
    """
    text = _EMAIL.sub("[REDACTED-EMAIL]", text)
    text = _SSN_SHAPE.sub("[REDACTED-ID]", text)
    text = _DIGIT_RUN.sub(_scrub_run, text)
    for token in names:
        text = re.sub(rf"(?<!\w){re.escape(token)}(?!\w)", "[REDACTED-NAME]", text, flags=re.IGNORECASE)
    return text


def _scrub_run(match: re.Match) -> str:
    run = match.group(0)
    if sum(ch.isdigit() for ch in run) < _MIN_SCRUB_DIGITS or _ISO_DATE_SHAPE.fullmatch(run):
        return run
    return "[REDACTED-NUMBER]"
