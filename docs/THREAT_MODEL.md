# Initial threat model — synthetic procurement v1

Assets: tenant records, contract contents/ACLs, connector secrets, approval integrity, outbound reputation, audit evidence and model budget.

Trust boundaries: browser/API, API/database, workflow/agent, agent/tool gateway, retrieval/untrusted contract, connector/external provider, app/telemetry. All model output and document content are untrusted. Server resolves identity; agent cannot grant itself privileges.

| Threat | Required control | Golden case / verification |
|---|---|---|
| Tenant header/ID spoofing | Tenant from verified identity; scoped queries and composite ownership constraints | B12; current API scoping tests |
| Document ACL bypass | Filter before ranking and before content return, including citations | B13; future retrieval tests |
| Malicious contract instructions | Treat text as evidence, validate all tool arguments through gateway | B14; future adversarial test |
| Self-approval or forged role | Independent approver and server policy checks | B07/B11; future policy tests |
| Approval replay/payload substitution | Exact canonical hash, expiry and evidence version binding | B08/B09/B10 |
| Duplicate delivery / concurrent workers | Stable key, unique ledger constraint, provider reconciliation | B16/B17 |
| Uncertain provider response | Reconciliation, no blind resend | B17 |
| Crash loses workflow state | Durable history; idempotent projection and dispatch | B18 |
| Secret/PII leak in traces | Redaction, restricted trace ACL, no raw secrets in model context | Inspect emitted telemetry before release |
| Hostile files and resource exhaustion | Size/type limits, sandbox parser, bounded task/token/retry budgets | B20; later parser/fuzz tests |
| SQL/tool argument escalation | Parameterized SQL, typed tool schemas, allowlisted capabilities | Current SQL reads; future tool contract tests |
| Sensitive financial action | No payment/account-change connector in MVP; policy deny | B20 |

Audit is append-only at the application interface with restricted writer permissions planned; current logs are not immutable. Production controls require separate review, retention/backups and incident response procedures. Residual risks include imperfect extraction, incomplete source data and external provider limitations; uncertainty must be surfaced rather than hidden.
