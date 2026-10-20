"""Corrections supersede older versions (TASK §4.7). History is kept: only the derived status changes."""
from __future__ import annotations

from collections.abc import Iterable

from ges_intake.config import Library
from ges_intake.models import QUARANTINED, Document


def apply(documents: Iterable[Document], library: Library) -> None:
    """Recompute `superseded_by` for every document from scratch.

    The relation depends on `received_at`, not on ingest order, so it is recomputed over the
    whole history after each batch: a correction received earlier than the version already
    held arrives superseded.
    """
    candidates = [d for d in documents if d.doc_type is not None and d.base_status != QUARANTINED]
    for older in candidates:
        older.superseded_by = tuple(sorted(newer.document_id for newer in candidates
                                           if newer is not older and _supersedes(newer, older, library)))


def _supersedes(x: Document, y: Document, library: Library) -> bool:
    x_type = library.doc_type(x.doc_type)
    if x_type.supersede_key is None or y.doc_type not in x_type.supersedes:
        return False
    for key in x_type.supersede_key:
        if key not in x.fields or key not in y.fields or x.fields[key] != y.fields[key]:
            return False
    return (x.received_at, x.document_id) > (y.received_at, y.document_id)
