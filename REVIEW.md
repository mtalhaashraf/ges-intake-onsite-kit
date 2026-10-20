# Code Review: legacy_tagger.py

## Findings

### 1. SQL Injection (Critical) — lines 86-91
Client ID and filename go straight into SQL with f-strings. Anyone uploading a file named `'; DROP TABLE evidence;--` wipes the database.
Fix: use parameterized queries (`?` placeholders).

### 2. Hardcoded API Key (Critical) — line 17
`sk-live-7f3a9c2e41b8d6` is a live key sitting in source code. If this repo is pushed anywhere, the key is leaked.
Fix: environment variable or secrets manager.

### 3. Path Traversal (High) — line 37
`os.path.join(UPLOAD_ROOT, client_id, filename)` with no validation. A filename like `../../etc/passwd` writes outside the upload directory.
Fix: validate filename, reject path separators, use `Path.resolve()` and check it stays inside UPLOAD_ROOT.

### 4. Provenance from Filename (High) — lines 30-33, 54-58
`proof_level()` decides provenance by checking if the filename contains "irs" or "bank". A user can name their file `irs_fake.txt` and get P1 trust. Provenance must come from the channel, not the filename.

### 5. Classification from Filename (High) — lines 44-51
`guess_cel()` matches keywords in the filename. A file named `w2_vacation_photos.txt` gets tagged as CEL-INC-001. Classification should use document content with proper fingerprinting.

### 6. Float for Money (Medium) — line 98
`float()` for dollar amounts. `float("48250.10")` can become `48250.09999...` which breaks balance comparisons. Must use `Decimal`.

### 7. Unredacted Text Sent to External API (Medium) — lines 61-63
`ask_model()` sends raw document text containing SSNs, EINs, account numbers to `ml-gateway.internal`. No redaction. This violates data handling rules.

### 8. Raw Text Printed to Console (Medium) — line 93
`print(f"... text={text[:200]!r}")` dumps the first 200 chars of every document to stdout. If the document has an SSN, it's now in server logs.

### 9. Readiness Set Without Evidence Check (Medium) — line 91
`UPDATE readiness SET status = 'READY'` runs whenever ANY document is tagged. One W-2 arriving marks the entire CEL as READY even if coverage is incomplete. Readiness needs proper pathway/coverage evaluation.

### 10. Mutable Default Argument (Low) — line 61
`def ask_model(text, hints=[])` — the list is shared across all calls. Every call appends "prefer CEL-TAX-001 when unsure" to the same list, so after 100 calls, hints has 100 copies.

### 11. Global DB Connection (Low) — line 14
`DB = sqlite3.connect(...)` at module import. No connection pooling, no cleanup, `check_same_thread=False` hides threading bugs.

### 12. Non-determinism (Low) — lines 84, 107
`datetime.datetime.now()` used for both `tagged_at` and `is_expired`. Output changes depending on when you run it. Should use an explicit `as_of` date parameter.

## Verdict

This should **not** stay in production. The SQL injection alone is enough to pull it — one malicious filename compromises the database. The hardcoded API key needs immediate rotation regardless.

First thing I'd do: kill the SQL injection by switching to parameterized queries, rotate the API key, and add path validation on the filename. Then plan a proper replacement using the library-driven classification approach.
