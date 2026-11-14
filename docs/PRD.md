# Product Requirements Document (PRD)

## Version and status
Version 0.1 — proposed requirements, not implemented.

## Personas and user stories
| ID | Persona | User story | Priority |
|---|---|---|---|
| US-01 | Procurement manager | Submit a goal and review an execution plan | P0 |
| US-02 | Analyst | View evidence-linked supplier delay findings | P0 |
| US-03 | Approver | Approve/reject proposed outbound emails | P0 |
| US-04 | IT admin | Configure scoped ERP/document connectors | P0 |
| US-05 | Auditor | Inspect immutable-style append-only operation history | P0 |
| US-06 | Manager | Schedule recurring investigations | P1 |
| US-07 | Admin | Manage tenant policies and roles | P0 |

## Core functional requirements
### FR-01 Authentication and tenancy
Every request must carry authenticated identity, tenant context and authorized role; cross-tenant access must be rejected server-side.

### FR-02 Goal intake and plan review
Accept a natural-language objective; validate scope; generate a typed task DAG with dependencies, required tools, budgets and acceptance conditions. User can confirm or revise the proposed plan.

### FR-03 Agent orchestration
Dispatch tasks to approved agent capabilities. Persist execution state, task outcomes, evidence references and structured errors. Do not allow the planner to expand tool privileges.

### FR-04 Data and document retrieval
Read permitted ERP records; ingest supplier contracts; support hybrid retrieval and source citations. Missing or contradictory sources must be flagged rather than silently resolved.

### FR-05 Business analysis
Compute lateness, contract grace periods and risk indicators using deterministic business logic; LLMs may interpret or summarize results but must not be the numerical source of truth.

### FR-06 Approval and controlled execution
Require policy checks and appropriate approval for external email or any side effect. Approval is bound to an exact action payload and expires; changed payloads require reapproval.

### FR-07 Observability and audit
Expose per-operation status, task traces, errors, costs, evidence and approval decisions. Store a tamper-evident or append-only audit trail where feasible; avoid describing ordinary logs as immutable.

### FR-08 Resilience
Implement bounded retries, timeouts, cancellation, idempotency and safe resumption after worker restart.

## Non-functional requirements
- **Security:** least privilege; encrypted transport; secret storage; prompt-injection defenses; tenant-aware retrieval.
- **Reliability:** no unapproved side effects; explicit states for partial completion and failure.
- **Performance:** report p50/p95 latency and throughput against a defined workload; no unverified SLA.
- **Maintainability:** typed schemas, contract tests, modular services, CI.
- **Accessibility:** keyboard navigability and meaningful status messages for UI.
- **Privacy:** data retention controls and redaction of secrets/PII in telemetry.

## Operation state model
`DRAFT -> PLANNED -> RUNNING -> WAITING_APPROVAL -> RUNNING -> COMPLETED`

Alternate terminal states: `FAILED`, `CANCELLED`, `PARTIALLY_COMPLETED`. Reject invalid transitions.

Phase 0 refinement: also include `WAITING_DOCUMENT`, `WAITING_CLARIFICATION`, `RECONCILING`, `CANCEL_REQUESTED` and terminal `REJECTED`. The guarded semantics, expiry defaults and role matrix are specified in [PHASE_0_SPEC.md](PHASE_0_SPEC.md); the simple path above is illustrative only.

## Acceptance criteria
1. A manager can submit a goal and inspect a validated plan before execution.
2. All findings link to a source record or document excerpt; unsupported conclusions are flagged.
3. An email cannot be sent without a matching policy authorization and required approval.
4. Replaying an approved execution does not send the same email twice.
5. A worker restart does not silently lose operation state.
6. A user from Tenant A cannot read Tenant B's contracts, tasks or traces.
7. A missing contract produces an explicit incomplete-evidence outcome.
8. A test report summarizes results and failures without fabricated metrics.

## UX surfaces
Workspace setup; goal composer; plan review; live operation detail; evidence panel; approvals inbox; connector administration; audit and evaluation dashboards.

## Dependencies and open questions
- Identity provider: managed OIDC or self-hosted alternative?
- Initial ERP: synthetic adapter first, then which production system?
- Contract formats and OCR scope?
- Approval policy ownership and retention requirements?
- Hosting and data residency constraints for pilot customers?
