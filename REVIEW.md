# Code Review: legacy_tagger.py

## Findings

### 1. SQL Injection (Critical)
Client ID and filename go straight into SQL with f-strings. Anyone uploading a file named `'; DROP TABLE evidence;--` wipes the database.
Fix: use parameterized queries (`?` placeholders).

### 2. Hardcoded API Key (Critical)
`sk-live-7f3a9c2e41b8d6` is a live key sitting in source code. If this repo is pushed anywhere, the key is leaked.
Fix: environment variable or secrets manager.

### 3. Path Traversal 
`os.path.join(UPLOAD_ROOT, client_id, filename)` with no validation. A filename like `../../etc/passwd` writes outside the upload directory.
Fix: validate filename, reject path separators, use `Path.resolve()` and check it stays inside UPLOAD_ROOT.

### 4. Provenance from Filename (High)
`proof_level()` decides provenance by checking if the filename contains "irs" or "bank". A user can name their file `irs_fake.txt` and get P1 trust. Provenance must come from the channel, not the filename.


### 5. Float for Money (Medium) —
`float()` for dollar amounts. `float("48250.10")` can become `48250.09999...` which breaks balance comparisons. Must use `Decimal`.

### 6. Raw Text Printed to Console (Medium) 
`print(f"... text={text[:200]!r}")` dumps the first 200 chars of every document to stdout. If the document has an SSN, it's now in server logs.

### 7. Readiness Set Without Evidence Check (Medium) 
`UPDATE readiness SET status = 'READY'` runs whenever ANY document is tagged. One W-2 arriving marks the entire CEL as READY even if coverage is incomplete. Readiness needs proper pathway/coverage evaluation.


### 8. Global DB Connection (Low) 
`DB = sqlite3.connect(...)` at module import. No connection pooling, no cleanup, `check_same_thread=False` hides threading bugs.


## Verdict

This should **not** stay in production. The SQL injection alone is enough to pull it — one malicious filename compromises the database. The hardcoded API key needs immediate rotation regardless.

