# NOTES

## Run

```bash
python tools/make_inbox.py inbox
python -m ges_intake --library library --clients clients run --client C-1001 --inbox inbox/day1 --inbox inbox/day2 --as-of 2026-03-03 --outbox outbox.jsonl
python -m unittest discover -s tests -t .
```

Run commands from the project root.

## Layout

- `library.py`: Loads and validates the library and client profile.
- `intake.py`: Validates manifests, files, timestamps, paths, and intake rules.
- `govern.py`: Classifies documents, extracts fields, checks identity, and assigns status.
- `readiness.py`: Checks document validity, missing evidence, balances, and service readiness.
- `attention.py`: Generates NOW, NEXT, and WATCH actions.
- `engine.py`: Connects all stages, generates document IDs, and handles advisory suggestions.

## Status

- Tier 1, 2, 3, and the stretch goal are implemented.
- Day 1 results match expectations: 20 documents, 1 duplicate, 8 rejections, and 1 ignored file.
- Day 2 corrects superseded documents, fills evidence gaps, and provides identity evidence.
- A03 remains `PENDING_REVIEW`, and the service remains `BLOCKED` because some issues are unresolved.
- Results are deterministic across runs and time zones.
- Tests are in `tests/test_engine.py`.

## Decisions

- **Client validation:** Client IDs and profiles are validated before processing.
- **Exit code 2:** Invalid libraries, clients, or manifests stop processing before any batch changes the outbox.
- **Timestamps:** ISO timestamps must include a timezone offset. A trailing `Z` is supported.
- **File safety:** Paths must stay inside the inbox. Symlinks and unsafe files are rejected, and file reads are size-limited.
- **Money parsing:** Values must have at most two decimal places and one valid negative marker.
- **Identity mismatch:** The document is quarantined, extracted fields are discarded, and further checks stop.
- **Field validation:** Name and account checks run only when those fields are successfully parsed.
- **Library validation:** Invalid JSON, missing keys, unknown references, invalid regexes, and dependency cycles are rejected.
- **Sensitive data:** Full SSNs, EINs, and account numbers stay in memory and are excluded from reports, errors, and the outbox.
- **Supersession:** Documents are compared after every batch using their received timestamps. Pending-review documents can supersede older documents.
- **Balance checks:** Balance continuity is skipped when either balance is missing.
- **Advisory:** Only unclassified documents receive suggestions. Digits are redacted, suggestions are never automatically applied, and write failures are recorded.
- **Attention:** Every pending-review document becomes a review item.
- **Storage:** Data is kept in memory; there is no persistent database.

## Next Steps

- Redact names and addresses before sending advisory text.
- Add persistent storage to retain history between runs.
- Add tests for symlinks, engagement-period coverage, and unknown library references.