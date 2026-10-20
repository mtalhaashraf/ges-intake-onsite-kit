import re
import unicodedata


def normalise_text(raw_bytes):
    """Normalised text: strip BOM, normalise line endings, apply NFKC."""
    text = raw_bytes.decode("utf-8")
    # Strip leading BOM
    if text.startswith("﻿"):
        text = text[1:]
    # Convert \r\n and lone \r to \n
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Unicode NFKC normalisation
    text = unicodedata.normalize("NFKC", text)
    return text


def phrase_view(normalised_text):
    """Phrase view: casefold + collapse whitespace runs to single space."""
    text = normalised_text.casefold()
    text = re.sub(r"\s+", " ", text)
    return text


def classify_document(phrase, document_types):
    """Match a document's phrase view against all document type fingerprints.

    Returns (matched_type_dict, flags_list):
    - Exactly 1 match: (the type, [])
    - Zero matches:    (None, ["no_match"])
    - Multiple:        (None, ["ambiguous_type"])
    """
    matches = []

    for dt in document_types:
        match_config = dt.get("match", {})
        all_phrases = match_config.get("all", [])
        none_phrases = match_config.get("none", [])

        # Every phrase in "all" must appear in the phrase view
        all_found = all(p.casefold() in phrase for p in all_phrases)
        # No phrase in "none" may appear
        none_found = any(p.casefold() in phrase for p in none_phrases)

        if all_found and not none_found:
            matches.append(dt)

    if len(matches) == 1:
        return (matches[0], [])
    elif len(matches) == 0:
        return (None, ["no_match"])
    else:
        return (None, ["ambiguous_type"])


def check_suspicious_patterns(phrase, patterns):
    """Check for suspicious instruction patterns (prompt injection).

    Returns ["suspicious_instructions"] if any pattern matches, else [].
    Patterns are case-insensitive regexes searched in the phrase view.
    """
    for pattern in patterns:
        if re.search(pattern, phrase, re.IGNORECASE):
            return ["suspicious_instructions"]
    return []
