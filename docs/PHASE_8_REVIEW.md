# Phase 8 — reviewed amendments and project delivery exposure

This extension adds order-scoped amendment proposals with independent human authorization and a delivery-exposure view grouped by project. It does not add inventory, project schedules, real ERP writes or automatic legal-signature verification.

## Amendment lifecycle

Upload the base contract normally and review it as the active source. Upload a separate PDF with kind `amendment`, explicit order IDs and an explicit amended delivery date. A grace period is optional in an amendment: absence preserves the reviewed base clause, never assumes zero. Reviewing this PDF prepares evidence only; it does not replace the active base or change calculations. OCR amendments retain their OCR comparison gate.

The requester chooses the base, amendment, order, effective date and an assigned independent company approver. Both users must have current access to both PDFs. The frozen hash binds the full order snapshot, reviewed terms, versions, checksums, document revisions, effective date, parties and any superseded amendment. Pending proposals expire after 24 hours. Only the assigned active approver can approve/reject; approval explicitly records comparison with the signed copy and authority for this order. This attestation is not cryptographic signature verification or a legal opinion. Current requester/approver roles, document ACLs, order values and source identity are rechecked before decisions and use.

Approved amendments apply only to the specified order and as-of dates on/after their effective date. A replacement requires the latest approved pointer and cannot become effective earlier than its predecessor. Earlier as-of dates follow the recorded chain. Concurrent approvals cannot silently overwrite each other. A changed order, source, authority or ACL fails closed; an inaccessible applicable amendment does not silently restore obsolete contract dates. The old ERP date remains unchanged and visible alongside the effective date and ERP lateness. Approved decisions remain audit records; changed authority requires a fresh proposal, not editing the old decision. Historical report snapshots remain recorded while new execution/dispatch requires current evidence. Per-order opaque authority fingerprints detect an amendment approved after a plan read its sources.

## Project exposure

`GET /v1/projects/exposure?as_of=YYYY-MM-DD&project_id=P1` groups current tenant purchase-order findings. It identifies overdue outstanding orders, shared overdue dependencies, high-risk findings and incomplete evidence, with original PDF citations. Verified agent reports include the same deterministic grouping. It does not sum quantities of unrelated materials: aggregate quantity, inventory shortage and project delay days are explicitly null. Delivery exposure is not proof of a delayed construction schedule, a stock shortage or a financial penalty. Approvers retain assigned-case evidence access and do not receive this procurement-wide view.

## UI and storage

The Arabic workspace adds document-kind selection, amendment proposal/review and project-exposure views. Session/access failures clear private views, and human comparison resets on reload. Source-text review and amendment-authority approval are separate actions. Migration `0008` defaults old documents to `contract`, adds constrained proposals/current pointers and preserves existing PDF bytes/pages/ACLs. Back up before upgrading; deployment still uses the bounded demo stack described in DEPLOYMENT.md. No cloud service or model key is needed for these deterministic features.

## Validation and boundary

Phase 8 regressions cover pending/rejected/future amendments, independent assignment and signed-copy attestation, hashes, stale orders/ACLs, revoked roles, inaccessible evidence, idempotency, expiry, effective scope, supersession, concurrent approval, historical reports and unknown inventory/schedule values. CI repeats against PostgreSQL, checks JavaScript, exercises an HTTP Compose scenario, verifies backup/restore and reruns existing security/OCR/Temporal regressions. Actual validation outcomes are in the PR checks and per-commit JSON artifacts, not inferred from this feature description. Browser visual validation depends on the computer-use runtime being available.

Phase 7 is merged. Phase 8 remains a separate review branch until explicitly merged. Phase 9 (controlled pilot and enterprise identity) remains pending actual organization/provider configuration.
