# NOTES

## Status

| Tier | State |
|---|---|
| 1: manifest, intake, IDs, duplicates, classification, extraction and masking, identity, provenance, grade, status, exit codes | Done, tested |
| 2: readiness: pathways, currentness, coverage gaps, PDA issues, precedence, dependencies, service status | Done, tested |
| 3: supersession, balance continuity, redacted advisory, library validation, determinism | Done, tested |
| Stretch: attention per role | Done, tested |

```bash
python tools/make_inbox.py inbox
python -m ges_intake --library library --clients clients run --client C-1001 \
       --inbox inbox/day1 --as-of 2026-03-01 --outbox outbox.jsonl
python -m unittest discover -s tests          # 35 tests, about 5 s
```

**Day 1** matches §5 exactly: 20 documents, 1 duplicate, 8 rejections, 1 ignored; A01 SATISFIED,
A02 MISSING, A03 MISSING (4821 March; 9930 2025-02-28), A04 PENDING_REVIEW, A05 BLOCKED_BY_DEPENDENCY;
service BLOCKED by the 1099-INT belonging to another taxpayer.

**Day 1 + day 2** (as of 2026-03-03): the W-2c supersedes the W-2 and the corrected February
statement supersedes the short one. The passport satisfies A02. March 4821 and the corrected February
close both gaps, so A03's pathway is met, but A03 stays PENDING_REVIEW: the 9930 May opening balance
(2,100.00) still does not match April's close (2,000.00), and the 7777 statement still needs review.
A05 therefore stays blocked, and the service stays BLOCKED by the quarantined 1099-INT. Running the
batches in the other order gives the same final documents and readiness. In that order the old
versions *arrive* superseded, as §4.7 requires.

## Structure

One module per stage, and `engine.py` wires them together:

`config` (library and client, validated) → `manifest` (order) → `intake` (checks, bounded read) →
`engine.document_id` (identity, duplicates) → `classify` → `fields` (typed extraction and masking) →
`govern` (identity, provenance, grade, status) → `supersede` → `readiness` → `attention`.
`advisory` wraps the vendor stub. `models.Document` keeps the governed status on arrival
(`base_status`) apart from the derived `superseded` state, so supersession never rewrites history.

Nothing about service 4A or any PDA, CEL or document-type ID is in the code. `tests/test_engine.py`
`DataDrivenTest` renames every library ID and checks the output is the same apart from the names.

## Decisions where the spec was open

**Input validation and exit 2**
- The library, the client and *every* manifest are validated before anything is processed, so an
  exit-2 condition in the second inbox leaves the outbox untouched ("change nothing").
- Client IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` before they touch a path, and the profile
  must carry exactly that ID. The second check also stops `c-1001` from resolving to `C-1001.json` on
  a case-insensitive file system.
- A manifest is malformed (exit 2) if it is not a JSON object, has no string `batch_id`, has no
  `entries` list, or has an entry that is not an object with a string `file` (there would be nothing
  to report as `source`). `manifest.json` must itself be a regular file, not a symlink.
- Library validation rejects:
  - unknown CEL, document-type, PDA or channel references, and dependency cycles;
  - duplicate IDs;
  - bad regexes, and patterns with too few capture groups;
  - unknown field types, currentness rules, coverage rules or P/E/C codes;
  - identity fields that are missing or of the wrong type, and supersede keys that are not extracted fields;
  - pathways with no requirements (they would be "met" with no evidence).
- Optional library values get defaults: `client_label` falls back to `name`, a missing criticality
  is treated as C1 (surface it rather than hide it), and a missing `tax_year` offset is 0.
- The client SSN may be full or masked. Accounts are reduced to their last four digits. Two accounts
  with the same last four make the profile invalid, because they could not be told apart.

**Timestamps and order**
- Accepted: `YYYY-MM-DDThh:mm[:ss[.fff]]` followed by `Z` or `±hh[:mm]`; a space instead of `T` is
  also accepted. Parsed by hand, because `fromisoformat` only takes `Z` from Python 3.11.
- An exact tie on (instant, file) is broken by the entry's canonical JSON, not its position, because
  manifest order means nothing. Reversing the manifest gives identical output (tested).

**Intake safety**
- `file` is normalised lexically (`..` escaping the inbox, an absolute path, NUL or empty →
  `unsafe_path`). Then the real path of its parent must be inside the inbox, which catches a symlinked
  directory on the way.
- A backslash → `unsafe_path`. It is a separator on Windows, so the same manifest would mean
  something else there.
- "Exists" requires the exact spelling as listed on disk. Otherwise `W2.TXT` would be accepted on
  macOS and missing on Linux, and runs would differ between machines.
- A dangling symlink is reported as `symlink`, not `missing_file`. A directory, FIFO or device →
  `unsafe_path`. A hard link (`st_nlink > 1`) → `unsafe_path`, since it may be the same inode as a
  file outside the inbox.
- Reads use `O_NOFOLLOW`, check that the inode is the one that passed the checks, and stop at
  `max_bytes + 1`. Files over the limit are rejected from `lstat` without being read at all.
- An OS error while reading (e.g. permission denied) is reported as a new reason, `unreadable`,
  rather than mislabelled as one of the listed reasons.
- `ignored` lists every top-level non-directory (hidden files and symlinks included) that no entry
  names. An entry counts as naming a file even if the entry was rejected.

**Classification and extraction**
- Library phrases go through the same NFKC, casefold and whitespace collapsing as the phrase view.
- A capture group that did not take part in the match → `unparseable_field`.
- Money with sub-cent precision (`1.005`) is `unparseable_field`: rounding would be guessing.
  `-0.00` is stored as `0.00`.
- Dates use a fixed English month table instead of `strptime("%B")`, which depends on the locale.
  All digit classes are ASCII (`int()` accepts Arabic-Indic digits).
- Text fields, document `source` names and `ignored` names are scrubbed of identifier-shaped numbers
  (five or more digits, ISO dates excepted) before they are output. A file called
  `w2_900-12-3456.txt` must not print an SSN in `source`. No generated file name is affected.

**Governance**
- A quarantined document keeps the flags it had already collected (`suspicious_instructions`,
  extraction flags) plus `identity_mismatch`, and persists no fields.
- The name check is skipped when the name field is absent (`missing_required_field` covers it if the
  field is required). The account check compares last four digits.
- The spec doesn't say what grade P4 (prepared by the firm) gets; I chose E3: it is not independent evidence.
- An unclassified document is always `pending_review`, even under a policy that leaves `no_match`
  out of `review_flags`.

**Supersession**
- Recomputed over the client's whole history after every batch, from `received_at`.
- Key fields are compared on their stored values, so EINs and accounts compare by last four digits.
  Two different accounts at one bank that share a last four could wrongly supersede each other; see
  "Next" below.
- A `pending_review` correction can still supersede: only quarantine excludes a document. A version
  that is itself superseded still supersedes anything older.

**Readiness**
- `as_of` is used for currentness only. Documents received after `as_of` still count, because
  nothing in §4.9 filters on `received_at`.
- The account of a document comes from the type's `identity.account_field`, else a field called `account`.
- If an adjacent pair of statements is missing either balance, that counts as a break: continuity
  is unproven.
- A per-account requirement for a client with no profile accounts is met with no evidence. That is
  the literal rule; I'd raise it with the library owners.
- An unclassified pending document has no CEL, so it raises no PDA issue, but it does appear in
  attention as an open review issue (§4.12: "every pending_review document").

**Output**
- Batch documents carry two extra keys. `fields` holds the typed, masked values. `advisory` holds the
  recorded suggestion, which always has `"applied": false`, or null. Everything else follows §3.
- `account` in readiness, and every account in messages, is the last four digits.

**Advisory**
- The text is the normalised document with identifier-shaped numbers, e-mail addresses and the
  client's name tokens redacted. Masked last-four forms (`XXX-XX-3456`) are allowed by §4.5 and pass through.
- The answer is checked for type and stored. Nothing reads it except the reviewer's attention
  message. If the outbox can't be written, the document records `"error": "advisory service unavailable"`.

**Attention**
- Security incidents use the key `issue:document:<id>` and kind `security_incident`.
- Client items carry `level`, `kind` and `message` only. Messages use the CEL `client_label`, ISO
  dates and "account ending NNNN". A test asserts that client items contain no `CEL-`, `PDA-`,
  `DT-` or `DOC-` IDs, no P/E/C/S/RU/RV codes, and no "quarantine".

**Inconsistencies in the kit** (noted, not changed):
- `review/legacy_tagger.py` calls the review "Part C"; TASK.md calls it Part B.
- The note in `document_types.json` cites "section 5.3"; the rule is §4.4.
- §4 skips 4.8, 4.10 and 4.11, and the pipeline diagram skips stage 6.

## Known limitations

- Tested on Python 3.13 (macOS) only. The code avoids 3.11+ APIs, but I could not run 3.10 here.
- The SSN identity check only runs for classified types with an `ssn_field`. Another taxpayer's
  document that fails classification is `pending_review`, not quarantined. A generic SSN scan would
  catch it but would change statuses against the spec, so it is only listed under "Next".
- Redaction is pattern-based. Names of *other* people in an unclassified document still reach the outbox.
- There is no review command, so pending documents and balance breaks can't be resolved inside a run.
- No persistence: every run rebuilds state from the inboxes given.
- The GL export contains a spreadsheet formula (`=HYPERLINK(...)`). Nothing here writes CSV, but any
  future export must escape cells starting with `=`, `+`, `-` or `@`.

## Next

1. Run the suite on Python 3.10, and add a fixture generator for a second, unseen-style library.
2. A review command (resolve a document or a discontinuity, appended as history) so attention items calm down.
3. Supersession keys compared on vaulted full identifiers instead of last four digits.
4. A pre-classification SSN scan that quarantines any document carrying an SSN other than the client's.
5. For production: inbox events on a queue, documents and an append-only event log in a database
   (one row per state change, keyed by client and partitioned per client), raw files and full
   identifiers in a separate vault service, and the AI call behind an egress proxy that enforces redaction.
