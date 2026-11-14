# Technical Design

## Service boundaries
- **API service:** auth context, goal intake, operation queries, approval endpoints.
- **Operation worker:** Temporal workflows and activities.
- **Agent runtime:** LangGraph planner, specialist capabilities and verification.
- **Tool gateway:** tenant-scoped allowlists, argument validation, credentials and rate limits.
- **Knowledge service:** document parsing, chunking, indexing, retrieval and provenance.
- **Policy/approval service:** deterministic action authorization, approval lifecycle.
- **Telemetry pipeline:** traces, metrics, logs and audit events with redaction.

## Initial agent capabilities
| Capability | Input | Output | Constraints |
|---|---|---|---|
| Planner | Goal, permitted tool catalog | Typed task DAG | No execution privileges |
| ERP retrieval | Query specification | Records + source IDs | Read-only, tenant scoped |
| Document analysis | Contract references | Clauses + citations + confidence | Abstain on missing evidence |
| Procurement analysis | Records + clauses | Deterministic delay/risk calculations | Business rules versioned |
| Verification | Findings + evidence | Validity report, unresolved issues | Independent checks |
| Communication drafting | Approved findings | Draft email payload | No send privilege |

## Typed plan contract (illustrative)
```json
{
  "operation_id": "op_123",
  "version": 1,
  "tasks": [
    {
      "task_id": "fetch_orders",
      "capability": "erp.purchase_orders.read",
      "depends_on": [],
      "inputs": {"status": "open"},
      "max_attempts": 3
    }
  ],
  "requires_review": true
}
```

Validate DAG acyclicity, tool scopes, task count, runtime budget and schema before scheduling.

## Proposed REST API
| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/operations` | Create operation from goal |
| GET | `/v1/operations/{id}` | Retrieve status and results |
| POST | `/v1/operations/{id}/confirm` | Confirm reviewed plan |
| POST | `/v1/operations/{id}/cancel` | Request cancellation |
| GET | `/v1/operations/{id}/events` | Paginated event history |
| GET | `/v1/approvals` | List pending approvals |
| POST | `/v1/approvals/{id}/decision` | Approve or reject exact action |
| POST | `/v1/connectors` | Configure scoped connector |
| POST | `/v1/documents` | Upload tenant document |

**Security:** OIDC/JWT validation, object-level authorization, request limits, audit event for mutations. API responses should never expose raw connector secrets or unrestricted model traces.

## Data model (initial)
- `tenants(id, name, created_at)`
- `users(id, tenant_id, external_subject, role)`
- `operations(id, tenant_id, requester_id, goal, state, plan_version, created_at)`
- `tasks(id, operation_id, capability, state, input_ref, output_ref)`
- `evidence(id, tenant_id, source_type, source_ref, excerpt_ref, checksum)`
- `approvals(id, tenant_id, operation_id, action_hash, approver_id, state, expires_at)`
- `action_executions(id, tenant_id, idempotency_key, action_hash, receipt_ref, state)`
- `audit_events(id, tenant_id, actor_id, operation_id, event_type, timestamp, payload_ref)`
- `documents(id, tenant_id, object_key, acl, checksum, ingestion_status)`

Use tenant-aware indexes, foreign keys and retention policies; document ACLs are enforced at retrieval time.

## Side-effect protocol
1. Build canonical action payload and stable hash.
2. Evaluate policy using actor, tenant, tool, resource, amount and context.
3. Require approval matching the exact payload hash when indicated.
4. Generate idempotency key per logical action and persist pending execution.
5. Invoke connector and record provider receipt; reconcile uncertain outcomes before retrying.
6. Append audit event and update operation projection.

## Failure and recovery design
- Retry transient failures only; never blindly retry non-idempotent writes.
- Use timeout, exponential backoff and per-task attempt budgets.
- Treat external request timeout after submission as **unknown outcome** until reconciled.
- Dead-letter or quarantine repeatedly failing operations for manual review.
- Prevent autonomous escalation of tool permissions or spending budgets.

## Retrieval design
Ingest authorized PDFs/text, preserve document version and location, chunk with metadata, index embeddings and optionally keyword search. Filter by tenant and ACL before retrieval. Rerank if evaluation shows benefit. Return citations with stable source IDs and versioned excerpts. Apply prompt-injection controls to retrieved text.

## Testing strategy
Unit: schemas, business rules, policy decisions. Integration: adapters and database tenancy. Contract: API/tool schemas. E2E: user goal to approved action. Chaos: timeout, crash, duplicate event. Security: cross-tenant and prompt-injection attempts. Evaluation: golden cases and regression thresholds.

## Suggested repository evolution
```text
apps/web/
services/api/
services/agent-runtime/
services/operation-worker/
services/knowledge/
packages/schemas/
packages/connectors/
infra/docker/
infra/kubernetes/
tests/unit/
tests/integration/
tests/e2e/
evals/datasets/
evals/reports/
docs/
```
These paths are **planned**, not implemented in the documentation-only starter.
