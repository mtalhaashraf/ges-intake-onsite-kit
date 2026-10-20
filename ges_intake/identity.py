import hashlib


def compute_sha256(raw_bytes):
    """SHA-256 hex digest of the raw file bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def compute_document_id(client_id, sha256):
    """Generate document ID: DOC- + first 12 hex chars of sha256(client_id:sha256).

    The client_id is baked in so the same file uploaded by two different
    clients gets two different document IDs — client data never collides.
    """
    combined = f"{client_id}:{sha256}"
    return "DOC-" + hashlib.sha256(combined.encode("utf-8")).hexdigest()[:12]


def is_duplicate(document_id, seen_ids):
    """Check if this document ID was already seen (this batch or earlier).

    Returns True if duplicate. If not, adds it to the seen set.
    """
    if document_id in seen_ids:
        return True
    seen_ids.add(document_id)
    return False
