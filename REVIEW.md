# Review: `review/legacy_tagger.py`

Reviewed as a pull request that would keep this module on the upload path for real client data.
Most serious first.

## Critical

**1. SQL injection through every value written (L86–91).**
`client_id`, `path` (which contains the uploader's file name), `cel` (which can come from the external
model) and `confidence` are pasted into SQL with f-strings. *Fails:* a model answer, or a crafted
file name reaching L91, of `CEL-X' OR '1'='1` turns the UPDATE into
`WHERE (client_id = 'C-1001' AND cel = 'CEL-X') OR '1'='1'`, which marks every readiness row of
every client `READY`. A file name containing `'` breaks the INSERT, and a crafted one sets `cel` and
`proof` to anything the attacker likes. Use parameterised queries (`?` placeholders) everywhere.

**2. Path traversal and silent overwrite on save (L36–41, L73).**
`filename` and `client_id` are joined into a path without any check, and the file is opened with `"wb"`.
*Fails:* `../../C-2002/w2.pdf` writes into another client's folder; an absolute name such as
`/etc/cron.d/job` makes `os.path.join` discard `UPLOAD_ROOT` entirely; a second upload called `w2.pdf`
overwrites the first, destroying evidence. That breaks "corrections add history, they never rewrite it".
Store under a server-generated name (e.g. the content hash), keep the original name as metadata only,
and validate `client_id` against a strict pattern.

**3. Client data and a live credential sent to an external service, in clear text (L16–17, L61–69, L78).**
The full, unredacted document text (SSNs, account numbers, names) goes to the model, together with a
hard-coded `sk-live-…` key, over plain `http://`. *Fails:* every W-2 that misses the filename rules ships
an SSN to a third party; anyone on the network path, or anyone who can read this repository, has the
key. Rotate the key today, load it from a secret store, use TLS, and redact identifiers before sending.

**4. Tagging a document marks readiness `READY` (L91).**
Any one upload whose guessed CEL matches flips the readiness row to `READY`. Nothing checks grade,
provenance, currentness, coverage, identity or review. *Fails:* a client uploads one bank statement
called `stmt.pdf` and the 12-statement requirement is "ready". "Having it ≠ ready" is the core rule
this violates. Readiness has to be computed from evidence, never set as a side effect of intake.

**5. The AI answer is trusted, and every failure becomes "tax return" (L61–69, L77–80).**
The model's `cel` goes straight into the database and readiness. `hints=["prefer CEL-TAX-001 when unsure"]`
biases it, and `except Exception` turns *any* failure (timeout, 500, bad JSON, revoked key) into
`CEL-TAX-001`. *Fails:* when the gateway is down, every unrecognised upload is filed as a prior-year tax
return. Separately, that fallback returns `confidence: None`, which L88 writes into SQL as the bare word
`None`, so SQLite raises `no such column: None` after the file was already saved at L73. The result is an
orphan file on disk with no record. Suggestions must be stored as advisory only, and errors must surface.

**6. Provenance is read from the file name (L30–33, L54–58, L81).**
`IRS transcript OFFICIAL.pdf` becomes P1 because of its name, and anything unrecognised defaults to P2
(independent third party). *Fails:* a client renames a self-typed document `irs_official.txt` and it is
treated as a government record. Provenance must come from the intake channel, never from what the
client called or wrote in the file.

## High

**7. No identity check: another taxpayer's document counts for this client (L72–94).**
Nothing compares the SSN or name in the document with the client. *Fails:* a 1099 for someone else,
uploaded to this client by mistake, is tagged, stored and (finding 4) marks the client's income
evidence ready. It should be quarantined and treated as a security incident.

**8. Classification is a substring guess on the file name (L19–28, L44–51).**
First-match substring rules: `"id"` matches `dividend.pdf`, `paid.pdf` and `provided.txt`; `"form"`
matches `information.pdf` and `platform_export.csv`; the text fallback files any document containing
"form" (including "inFORMation") as a tax return. `confidence = 1.0` (L76) then claims certainty.
The mapping is also hard-coded and "kept in sync (mostly)" with another file, so it drifts from the
evidence library. Classify on content fingerprints from the library and send no-match/ambiguous to review.

**9. Raw document text written to logs (L93).**
`text[:200]` of a W-2 includes the employee's SSN, so logs, log shippers and anyone with log access
receive full identifiers. Log IDs and outcomes, never content.

**10. Tax year guessed from the file name or the wall clock (L83–84).**
`invoice_2019_v2026.pdf` is filed as 2019. A file without a year gets `now().year - 1`, so the same
upload is tagged 2025 on 31 December and 2026 on 1 January. The system must not guess. A year it
cannot read should be missing and flagged.

## Medium

**11. Undecodable and oversized files are accepted silently (L72–74).**
`errors="ignore"` turns Latin-1 text or a JPEG into plausible-looking text that is then classified.
There is no size, type or empty-file check, and the whole upload is held in memory.

**12. Money as `float` (L97–102).**
`amount("($45.20)")` raises `ValueError` (parentheses are not handled); `"nan"` parses and never equals
itself; large balances lose precision. Use `Decimal` and an explicit parser.

**13. Expiry uses the local wall clock and a single format (L105–107).**
A licence expiring today is "expired" from 00:00:01, the result depends on the server's time zone
and run time, and any format other than `MM/DD/YYYY` raises. Compare `date`s against an explicit as-of date.

**14. One SQLite connection shared across threads, opened at import (L14).**
`check_same_thread=False` with no lock under a (presumably threaded) upload handler allows interleaved
transactions; importing the module creates `ges.db` in whatever the current directory is.

**15. No timeout on the HTTP call (L66).**
A slow gateway blocks the upload handler indefinitely.

**16. Mutable default argument (L61–62).**
`hints=[]` is shared across calls, so every call appends another copy of the hint: after 10,000
uploads each request carries 10,000 identical hints, and state leaks between clients.

## Low

**17.** `tagged_at` is a naive local timestamp (L89); `cel` may be stored as the string `'None'`
(L79, L88); duplicate uploads insert duplicate rows pointing at one overwritten file.

## Verdict

It should not stay on the upload path in this form. It can be exploited from a file name today (findings 1
and 2) and it leaks client identifiers and a live credential (3). Its outputs also cannot be trusted
for readiness (4–7). First, rotate the API key and switch off the external call and the readiness
`UPDATE`. Then parameterise the SQL and store uploads under generated names, so the module only
records files for human review until the governed intake replaces it.
