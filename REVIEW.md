Legacy CEL Tagger Code Review
Findings
1. Critical – SQL Injection
SQL queries use f-strings with untrusted input.
Recommendation: Use parameterized SQL queries.
2. Critical – Hardcoded API Key
API key is exposed directly in source code.
Recommendation: Use environment variables and rotate the exposed key.
3. High – Path Traversal
Untrusted filenames and client IDs are used to construct upload paths.
Recommendation: Validate paths and ensure they remain inside the upload directory.
4. High – Unsafe AI Fallback
Failed AI classification defaults to CEL-TAX-001.
Recommendation: Return an unclassified result and flag it for review.
5. High – Incorrect Provenance
Evidence trust levels are determined from filenames.
Recommendation: Determine provenance using trusted upload channels.
6. High – Automatic Readiness Approval
Documents automatically change readiness status to READY.
Recommendation: Let the readiness engine evaluate all required evidence.
7. Medium – Sensitive Data Exposure
Raw document text is printed to application logs.
Recommendation: Never log confidential document contents or secrets.
8. Medium – Financial Precision
Financial amounts use float, which can introduce precision errors.
Recommendation: Use decimal.Decimal for monetary calculations.
9. Medium – System Clock Dependency
datetime.now() controls tax-year defaults and expiration checks.
Changing the system clock can affect results.
Recommendation: Use an injected, consistent clock and timezone-aware timestamps.
Overall Assessment

Risk Level: HIGH

The legacy tagger has significant security, privacy, provenance and data-integrity issues.

Recommended Action
Retire the legacy tagger from the upload workflow.
Route documents through the new intake and evidence engine.