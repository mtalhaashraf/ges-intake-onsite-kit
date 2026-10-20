# Review: `review/legacy_tagger.py`

**1. Critical: Exposed API key (Lines 17, 63)**  
The API key is hard-coded in the code. Anyone with access could misuse it. Store it securely and rotate the existing key.

**2. Critical: SQL injection (Lines 86–91)**  
SQL queries use untrusted input, allowing attackers to modify or delete database records. Use parameterized queries.

**3. Critical: Unsafe file paths (Lines 37–40)**  
Malicious filenames could escape the upload folder and overwrite other files. Validate paths and restrict access to the upload directory.

**4. Critical: Incorrect readiness (Line 91)**  
Evidence is marked `READY` without checking completeness, validity, or review. Only mark it ready after all required checks pass.

**5. Critical: Sensitive data exposure (Lines 16, 63–69, 93)**  
Private document data is sent over unencrypted HTTP and printed in logs. Protect sensitive data and avoid logging document contents.

**6. High: Unsafe AI classification (Lines 68–80)**  
Model failures default to a tax-return classification, and model answers are trusted without verification. Send uncertain documents for human review.

**7. High: Missing identity verification**  
Documents are not checked against the client, so another person's evidence could be accepted.

**8. High: Unreliable classification and provenance (Lines 20–58)**  
Document types and official status are guessed from filenames or text keywords. Use reliable validation instead.

**9. Medium: Parsing and date issues (Lines 83–107)**  
Money parsing can fail, tax years can be incorrect, and expiration checks support limited date formats. Use robust parsing and explicit reference dates.

**10. Medium: File and network handling (Lines 14, 38–40, 61–74)**  
There are no file-size limits or duplicate checks, files can be overwritten, shared hints can accumulate, invalid bytes are ignored, and network requests can hang. Add validation, safe connection handling, and timeouts.

**Verdict:** The code is unsafe for production. First rotate the API key, disable unsafe model calls and automatic readiness updates, fix SQL injection and path traversal, protect client data, and require human review for uncertain evidence.