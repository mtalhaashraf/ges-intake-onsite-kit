import re


def redact_text(text):
    """Redact sensitive identifiers from text before sending externally.

    Removes: SSNs, EINs, account numbers, license numbers, passport numbers.
    """
    # Full SSNs: 123-45-6789
    text = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED-SSN]", text)
    # Masked SSNs: XXX-XX-1234 or ***-**-1234
    text = re.sub(r"\b[X*]{3}-[X*]{2}-\d{4}\b", "[REDACTED-SSN]", text)
    # EINs: 12-1234567
    text = re.sub(r"\b\d{2}-\d{7}\b", "[REDACTED-EIN]", text)
    # Account numbers: sequences of digits with optional spaces/dashes, 4+ digits
    text = re.sub(r"\b\d[\d \-]{3,}\d\b", "[REDACTED-ACCOUNT]", text)
    return text


def get_advisory_suggestion(norm_text, outbox_path):
    """Call the advisory stub with redacted text. Returns the suggestion dict."""
    from vendor.advisory_stub import request_suggestion
    redacted = redact_text(norm_text)
    return request_suggestion(redacted, outbox_path)
