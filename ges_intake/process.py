from datetime import datetime


def parse_timestamp(raw):
    """Parse an ISO 8601 timestamp that MUST have a UTC offset or Z.

    Returns a UTC datetime if valid, None otherwise.
    Per §4.2 check #1: only timestamps with an explicit offset are valid.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # Python 3.11+ handles 'Z' natively, but 3.10 does not
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            # No offset → invalid per the spec
            return None
        return dt
    except (ValueError, TypeError):
        return None


def sort_entries(entries):
    """Sort manifest entries into processing order per §4.1.

    1. Entries with a valid received_at: sorted by (UTC instant, file).
    2. Entries with an invalid/missing received_at: sorted by file.
    """
    valid = []
    invalid = []

    for entry in entries:
        dt = parse_timestamp(entry.get("received_at"))
        filename = entry.get("file", "")
        if dt is not None:
            valid.append((dt, filename, entry))
        else:
            invalid.append((filename, entry))

    valid.sort(key=lambda x: (x[0], x[1]))
    invalid.sort(key=lambda x: x[0])

    return [e for _, _, e in valid] + [e for _, e in invalid]
