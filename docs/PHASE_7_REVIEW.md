# Phase 7 — local scanned-PDF OCR

This is a new extension after the original six-phase roadmap, authorized on 16 November 2026. Phase 8 (amendments/project exposure) and Phase 9 (controlled pilot/enterprise identity) are separately pending.

## Behavior

Upload a scanned PDF through the existing evidence workspace. Its original version remains NEEDS_OCR. The owner can request `POST /v1/documents/{id}/ocr` with the current revision and a higher version number. This rasterizes every page locally at 200 DPI and invokes Tesseract with Arabic and English data. The derived version preserves the exact original PDF bytes/checksum and records the source document ID, engine version, language configuration and word-confidence summaries. It is private and unpublished by default; original pages, ACLs, active sources and old operation results are unchanged.

OCR text is evidence, never tool instructions. Existing typed clauses, source conflicts, hashes, page offsets, tenant/document ACLs and review gates still apply. Low-confidence or empty pages cannot be published. Otherwise the owner must explicitly confirm comparison against the original PDF before publication. This is a recorded human attestation, not automatic proof of correct transcription or a legal signature. OCR-derived citations/page responses label their text origin. A new reviewed source requires a new/refreshed operation snapshot.

## Setup and limits

The Docker image installs `tesseract-ocr`, `tesseract-ocr-ara` and `tesseract-ocr-eng`. Local Windows users need a trusted Tesseract installation with both languages; `OCR_TESSERACT_PATH` optionally names the executable. No cloud OCR service, API key or document transmission is used. Missing engines return a safe 503; capability checks do not disclose executable paths. Arabic support means the engine/language data are wired; accuracy on arbitrary Arabic contracts is not certified.

Original upload limits remain 2 MiB/20 pages; an explicit OCR job is limited to 5 pages, 8 million rendered pixels per page, 24 million total pixels, 12 seconds per engine invocation and a 65-second process-tree deadline. The process tree is monitored for 384 MiB RSS; Unix address-space/CPU/output-file bounds also apply. Credentials are stripped from its environment. This remains process isolation, not a complete OS exploit sandbox. Poor scans, handwriting, tables, rotation and ambiguous legal prose may require a clearer upload; no editing or automatic correction of OCR facts is implemented. Any word-confidence value below 60 blocks publication. Confidence is an engine heuristic, not a calibrated probability. This conservative threshold can reject otherwise readable documents.

`pypdfium2` rasterization and Pillow are explicit runtime dependencies already pinned by the lock file. Tesseract is supplied by the operating-system package repository; its actual version is recorded with each derived document. See [Tesseract command-line documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html) and [PDFium Python API](https://pypdfium2.readthedocs.io/en/stable/python_api.html).

## Verification

`python -m unittest discover -s tests -p test_phase_seven.py -v` exercises immutable versions, owner/tenant access, tampering, stale revisions, unavailable engines, low-confidence blocks, explicit comparison, reviewed cited assessment, TSV validation, pixel limits and process-tree timeout behavior. Local tests without Tesseract skip actual recognition; CI sets `REQUIRE_OCR=1`, installs the engine/languages and must recognize a raster-only synthetic English contract through the full OCR/review path on SQLite and PostgreSQL. This test is a defined fixture, not a broad OCR accuracy benchmark. Existing security, document, graph and Temporal regressions also remain required.
