
import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize document text."""
    text = text.removeprefix("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFKC", text)


def phrase_view(text: str) -> str:
    """Create a case-insensitive view with collapsed whitespace."""
    normalized = normalize_text(text)
    return " ".join(normalized.casefold().split())


def classify_document(
    text: str,
    document_types: list,
    suspicious_patterns: list
) -> dict:
    """Classify a document using configured matching rules."""

    view = phrase_view(text)
    matches = []

    for document_type in document_types:
        match_rules = document_type["match"]

        all_phrases = match_rules.get("all", [])
        none_phrases = match_rules.get("none", [])

        all_match = all(
            phrase.casefold() in view
            for phrase in all_phrases
        )

        none_match = not any(
            phrase.casefold() in view
            for phrase in none_phrases
        )

        if all_match and none_match:
            matches.append(document_type)

    flags = []

    if len(matches) == 0:
        flags.append("no_match")
        selected_type = None

    elif len(matches) > 1:
        flags.append("ambiguous_type")
        selected_type = None

    else:
        selected_type = matches[0]

    # Check every document, even if classification failed.
    for pattern in suspicious_patterns:
        if re.search(pattern, view, re.IGNORECASE):
            flags.append("suspicious_instructions")
            break

    return {
        "doc_type": selected_type,
        "cel": selected_type["cel"] if selected_type else None,
        "flags": flags
    }
