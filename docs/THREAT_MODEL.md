# Initial threat model — synthetic procurement v1

Assets: tenant records, contract contents/ACLs, connector secrets, approval integrity, outbound reputation, audit evidence and model budget.

Trust boundaries: browser/API, API/database, workflow/agent, agent/tool gateway, retrieval/untrusted contract, connector/external provider, app/telemetry. All model output and document content are untrusted. Server resolves identity; agent cannot grant itself privileges.

| Threat | Required control | Golden case / verification |
|---|---|---|
| Tenant header/ID spoofing | Tenant from verified identity; scoped queries and composite ownership constraints | B12; current API scoping tests |
| Document ACL bypass | Filter before ranking and before content return, including citations | B13; Phase 3 ACL/search/page/download/cached-operation revocation tests |
| Malicious contract instructions | Treat text as evidence, validate all tool arguments through gateway | B14; Phase 3 hostile-PDF-text test; document text is not sent to the planner |
| Self-approval or forged role | Independent approver and server policy checks | B07/B11; Phase 4 live-role and separation tests |
| Approval replay/payload substitution | Exact canonical hash, expiry and evidence version binding | B08/B09/B10; Phase 4 revision, tamper, expiry and live evidence tests |
| Duplicate delivery / concurrent workers | Stable key, unique ledger constraint, provider reconciliation | B16/B17; Phase 4 concurrent dispatch and one-message sandbox acceptance |
| Uncertain provider response | Reconciliation, no blind resend | B17; Phase 4 unknown-outcome test |
| Crash loses workflow state | Durable history; idempotent projection and dispatch | B18; real Temporal replay/restart and sandbox accept-before-ledger crash test |
| Secret/PII leak in traces | Redaction, restricted trace ACL, no raw secrets in model context | Inspect emitted telemetry before release |
| Hostile files and resource exhaustion | Size/type limits, sandbox parser, bounded task/token/retry budgets | B20; later parser/fuzz tests |
| SQL/tool argument escalation | Parameterized SQL, typed tool schemas, allowlisted capabilities | Current SQL reads; future tool contract tests |
| Sensitive financial action | No payment/account-change connector in MVP; policy deny | B20 |

Audit is append-only at the application interface with restricted writer permissions planned; current logs are not immutable. Production controls require separate review, retention/backups and incident response procedures. Residual risks include imperfect extraction, incomplete source data and external provider limitations; uncertainty must be surfaced rather than hidden.


Phase 3 controls: bounded multipart/PDF parsing in a credential-stripped child process, immutable file/page checksums, exact reviewed clause spans, ACL predicates before ranking plus checks before result return, current access checks on stored operation output, and source-version changes blocking report completion. The child is not a complete OS sandbox; malicious-parser exploits, antivirus/quarantine, DB-owner tampering, semantic retrieval quality and production isolation remain residual risks. Source publication requires its owner, but does not verify a legal signature or authorize an external action.

Phase 5 controls: dashboards, traces, costs and timelines enforce current owner/tenant or assigned-case access and PDF ACLs before returning records/counts. Timeline fields are explicitly allowlisted; planner telemetry stores bounded token counts and status/timing/rate metadata, never prompts, raw provider errors, credentials or private reasoning. Frontend responses are bound to the initiating session, affected views clear on denied access/logout, and changed proposals disable approval pending explicit review. Synthetic bounded reporting is not a production audit/retention system. Operator-provided price sources are captured estimates, not trusted invoices or proof of billing.

Phase 6 adds shared fixed-window database admission, bounded streamed JSON, exact host admission, optional fail-closed HTTPS/restricted deployment checks, safe correlated errors and file-mounted secrets. Application containers have read-only roots and bounded temporary resources. Residual risks include fixed-window boundary bursts, distributed request floods, password/account denial of service, DB-owner tampering, incomplete parser isolation and unsafe proxy trust configuration. The demo has no production OIDC or authenticated Temporal cluster. See DEPLOYMENT.md and PHASE_6_REVIEW.md; advisory scans are dated observations, not guarantees.
