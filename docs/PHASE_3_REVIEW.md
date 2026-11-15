# Phase 3 — document evidence and risk intelligence

Review date: 15 November 2026. This phase links procurement findings to reviewed PDF clauses and enforces document access independently of company/role access.

## Delivered behavior

| Area | Implementation |
|---|---|
| Ingestion | Authenticated multipart PDF upload, raw bytes/checksum stored in the database, immutable logical contract versions, extracted pages and overlapping chunks |
| Bounded extraction | 2 MiB/file, bounded request body, 20 pages, 200,000 text characters, eight-second parser child deadline, memory monitoring and Linux resource limits; provider keys are not inherited by the child |
| Candidate clauses | Conservative explicit English/Arabic grace/date/supplier labels; original quote, page, offsets and page-text hash retained |
| Review/publish | Owner confirms exact revision, file checksum and extraction hash; only unambiguous calendar-day clauses can become an active contract source |
| Source governance | Contract family owner/supplier are immutable across versions through relational constraints; replacing an active source requires its owner and cannot roll back the same family's version |
| ACL | Private by default; explicit reader IDs from the same tenant; no manager/admin automatic document access; tenant/ACL predicates precede retrieval and ranking |
| Retrieval | Local lexical BM25 ranking over allowed chunks, bounded corpus with an explicit limited flag, page-span integrity checked again before returning excerpts |
| Citations | Findings include PDF ID, logical reference, version, raw-file checksum, page, exact quote/span and text hash; viewer and original download recheck access |
| Assessment | Uses reviewed PDF terms when explicitly published, otherwise the original synthetic fixture; a denied/out-of-scope/expired active PDF never silently falls back to the fixture |
| Conflicts | Conflicting grace clauses or unsupported business-day terms cannot be published; ERP-vs-reviewed-PDF due-date conflicts yield unknown contractual risk and WAITING_CLARIFICATION |
| Verification | Rechecks values against cited clauses, exact page spans, source identity/current binding and live ACL; changed source/access blocks remaining execution/final report |
| Persisted results | Old reviewed versions remain reproducible; access is rechecked for saved task outputs, results and traces, so revocation cannot be bypassed through an old operation |
| UI | Upload and ready-made synthetic samples, candidates and page viewer, review/publish, reader sharing/revocation, text search and clickable report citations |

No file text is submitted to Gemini/OpenAI by this phase. Gemini remains the goal planner; the evidence service and numerical verifier run locally. The published contract link and effective/order scope are explicit user-reviewed metadata, not verified signatures or legal determinations.

## Validation

The suite contains 87 unittest methods: the previous 58 plus 29 Phase 3 methods. Tests exercise text/mixed-scanned/encrypted/malformed/oversize/many-page PDFs, explicit/no/unknown/Arabic grace, conflicting clauses, supplier/order linkage, private/other-tenant access, sharing/revocation, hidden-corpus ranking, stale review/version/hash, preserved migrations, poisoned extraction and page/term/file mutations, ERP conflicts, source changes during reads and final report assembly, cached-result/trace access, parser timeout/key isolation, and prompt-like text with zero outbound actions.

Six synthetic PDFs (eight total pages) were authored and all pages were rendered and visually inspected. They cover a page-2 S1 clause, missing-supplier S3 evidence, contradictory clauses, business days, image-only content and malicious instruction-like text. Fixture text is not treated as instructions. PDFs live under `evals/datasets/contracts/`.

The browser exercised a multipart sample upload, exact page-2 review, activation, real Gemini supplier-scoped plan, exact confirmation, all five tasks, completed findings, page navigation, search, saved results and logout. A real HTTP multipart upload was also verified directly; the browser automation's native file chooser could not attach the local file reliably, so sample upload was used for browser acceptance. A schema discrepancy from an early local migration was caught on the real server and repaired with additive migration 0004; a preservation test covers existing PDF/page/ACL data. The user's demo/company data was not replaced: browser/live QA used a separate synthetic tenant.

Real Gemini acceptance evidence is recorded in `evals/reports/phase3-live-gemini.json`. The plan was produced by `gemini-3.1-flash-lite`, used a reviewed PDF clause, and returned PO-A1 with 13 late days, 8 beyond grace and HIGH priority for the explicit fixture date 2026-11-14. Both contractual facts cite page 2 of version 1 with the original file checksum and exact quote. The test was run on 15 November 2026. It is one reference case, not a model-quality benchmark. The temporary QA account is disabled and its sessions revoked after inspection.

CI runs the Phase 3 methods again against PostgreSQL and extends real Compose acceptance with PDF multipart ingestion, publication, denied access, page-quote validation, lexical search, and persisted documents after API restart. Final branch/PR checks are the source of truth for execution results.

## Important scope limits

- Extraction handles explicit labelled clauses (for example `Grace period: 5 calendar days.` and ISO delivery dates), with owner review. It is **not** general legal-language interpretation or LLM contract extraction. Arbitrary/ambiguous prose remains NEEDS_REVIEW; contradictory terms need a clarified document version.
- OCR is not implemented. Empty/scanned pages block publication, including mixed PDFs with an empty-text page. Text extraction does not guarantee that every visually meaningful element was captured; review the original when necessary. No signature validity or signed-amendment verification is claimed.
- Retrieval is lexical, not semantic embeddings or pgvector. There is no broad semantic RAG claim or retrieval-quality benchmark. The current scoped reference cases need explicit supplier bindings and exact source passages.
- Source replacement and assessment use a single explicitly active document per supplier, optionally restricted to named orders and effective dates. There is no inferred multi-contract precedence, automated amendment selection, business holiday calendar or project-delay forecast.
- Private active sources remain unavailable to nonreaders; the output is incomplete evidence without leaking the denied file's ID/name/excerpt. Original mock terms remain available only where no PDF has been activated. Publication is a source-review action, not approval for sending or changing ERP data.
- Parser isolation/resource bounds are not a complete security sandbox, antivirus system or production file-ingestion certification. PostgreSQL RLS, dedicated runtime DB roles, object-storage hardening, retention/backups, rate limiting and advanced quarantine remain hardening work. Raw PDFs are stored as bounded database BLOB/bytea, not in S3.
- Long-running waits, document-arrival resume, external approvals and sending remain Phase 4. Uploading new evidence does not rewrite an existing result; create/review a new plan to use the new source. Existing operation snapshots keep their original evidence version, subject to current access.

## References

- [pypdf extraction limitations](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)
- [FastAPI file/form API](https://fastapi.tiangolo.com/tutorial/request-forms-and-files/)
