# Phase 0 — implementation contract v1

Status: planning baseline for the synthetic demo. Defaults below can be revised with versioned fixtures; they are not customer-approved legal or operating policies. The local API implements only the simple calculation subset. This document does not claim that future behavior already runs.

## Scope
One operation investigates outstanding supplier orders, reads authorized records/contracts, calculates delivery exposure, reports evidence, drafts supplier follow-ups and submits exact actions for independent approval. No payments, penalties, legal enforcement or contract amendments. Synthetic data only; no production integration required for Phase 0.

## Canonical input and source authority
- Every entity has tenant ownership, stable ID, source reference and version. Cross-tenant relationships are invalid, even when IDs coincide.
- Purchase order: ordered quantity, delivered accepted quantity, unit, current ERP due date, supplier, project allocations and status. Quantities must be nonnegative and units compatible; invalid data blocks dependent findings.
- Contract: supplier, effective dates, delivery/grace clause, day convention, version, source location and optional signed amendment linkage.
- Delivery records determine accepted delivered quantity. ERP supplies operational due dates; signed contract/amendments supply contractual terms. Neither silently overrides a conflicting source.
- A current signed amendment with a valid approval reference and matching ERP date may replace the prior due date. Unverified amendments or conflicting dates produce `WAITING_CLARIFICATION`; report the two dates, with no contractual risk calculation dependent on the conflict.
- Snapshot `as_of`, source versions and business-rule version for reproducibility. If relevant data changes before sending, invalidate the proposed action/approval and regenerate.

## Calculation rules
1. Use explicit ISO `as_of` date, not the machine clock. Calendar days are the fixture default. Business-day clauses require an explicit tenant holiday/weekend calendar; missing calendar blocks contractual calculation.
2. `remaining = max(0, ordered - accepted_delivered)`. Overdelivery is a data warning; no negative exposure. Closed/cancelled orders are excluded from open-order investigation, retaining an exclusion reason.
3. Partial deliveries reduce quantity exposure; the remaining quantity retains the agreed due date unless a verified delivery schedule assigns dates to individual lines. Do not invent line schedules.
4. Outstanding calendar-day lateness is `max(0, as_of - agreed_due_date)`. Due today and future dates are not late. Completed orders have zero current lateness; this does not establish historical on-time performance.
5. Beyond grace is `max(0, late_days - grace_days)`. An explicit no-grace clause means zero; absent/ambiguous terms are unknown, never assumed zero.
6. Missing contract/unknown grace yields `INCOMPLETE_EVIDENCE`, no beyond-grace value, no contractual risk level. Report factual ERP lateness separately, and block messages asserting breach or penalty.
7. Priority is HIGH for an outstanding critical material beyond grace; MEDIUM for noncritical material beyond grace; LOW otherwise. An unknown criticality yields an unresolved priority. This is a v1 triage score, not a legal judgment.
8. Project impact is an exposure list based on explicit order-line/project allocations. Sum only quantities with compatible units. List shared project exposure once per project with underlying order references; do not infer construction delay days without stock, consumption and schedule data.
9. Inventory shortage can only be asserted with available stock and demand data in compatible units at the same snapshot. Missing inventory means impact unknown, not zero.
10. Financial penalties and automatic account changes remain outside scope, even when an agent suggests them.

## Roles and resource scope
Permissions below always require same tenant and resource/document ACL. Roles alone cannot bypass ACLs. Separate role assignments may be combined, subject to separation of duties.

| Capability | Analyst | Procurement manager | Approver | IT admin | Auditor | Execution service |
|---|---|---|---|---|---|---|
| Create goal/review plan | Yes | Yes | No | No | No | No |
| Read procurement and permitted evidence | Yes | Yes | Assigned cases | No | No | Action minimum |
| Draft follow-up / request approval | Yes | Yes | No | No | No | No |
| Approve/reject outbound email | No | No* | Yes | No | No | No |
| Configure scoped connector/roles | No | No | No | Yes | No | No |
| Read redacted audit records | Own operations | Assigned operations | Assigned decisions | Configuration events | Yes | No |
| Dispatch an approved action | No | No | No | No | No | Yes |

*A manager needs a separate approver assignment; nobody approves their own request. The current local API has only manager/analyst procurement reads, not this full policy system.

## Approval/execution contract
- External email always requires independent human approval. No eligible approver means wait/block, never self-approve.
- Canonical action includes tenant, operation ID, logical action ID, connector, sender, exact recipients/CC/BCC, subject/body, attachment IDs/checksums and evidence versions. Serialize canonically and hash; normalize only before presenting for approval.
- Approval expires 24 hours after decision in demo policy v1. Bind approver, permission/policy version, action hash and expiry in UTC. Recheck permissions, policy and evidence immediately before dispatch.
- Changed recipients/body/attachments/evidence, revoked permission or expiry blocks dispatch and requires fresh review. Rejection is terminal for this proposed operation; a revision is a new linked operation.
- Persist one logical-action idempotency key and pending ledger entry before invoking provider. Concurrent/replayed attempts must converge on that entry.
- Known pre-submission transient failure: at most 3 attempts total, bounded backoff and operation budget. Provider-accepted receipt: record success and never resend automatically.
- Timeout after submission: `RECONCILING`. Use provider idempotency/lookup if supported; otherwise human reconciliation. An unknown outcome never becomes a blind retry or a claimed successful send.
- Durable state ownership remains Temporal; PostgreSQL stores domain data and UI projections. Phase 0 specifies the protocol; it does not implement Temporal or the ledger.

## Lifecycle semantics
The domain validator enumerates allowed transitions, but real transitions must also satisfy these guards:
- `DRAFT -> PLANNED`: plan validated; `PLANNED -> RUNNING`: authorized user confirmed exact plan version.
- `WAITING_DOCUMENT`: dependent tasks blocked, independent safe analysis can continue. Expiry after 7 days in the demo produces partial completion if useful supported results exist, otherwise failure. Both are explicit policy defaults.
- `WAITING_CLARIFICATION`: source conflict or ambiguous scope; no guessed resolution. Seven-day expiry follows the same partial/failure rule.
- `WAITING_APPROVAL`: exact proposal frozen; approval expiry leaves operation waiting for a fresh decision. Rejection yields `REJECTED`.
- `RECONCILING`: unresolved external outcome; retain operation until outcome is known or manually adjudicated. A timeout alone is not evidence of failure or success.
- Cancellation before dispatch yields `CANCELLED`. During an in-flight side effect, enter `CANCEL_REQUESTED`, reconcile and stop remaining work. Final cancellation report must include actions that already happened.
- `COMPLETED`: all required outcomes supported and requested actions resolved. `PARTIALLY_COMPLETED`: useful supported subset, explicit unresolved items, and no unknown external outcome. Terminal states do not restart; create a linked operation instead.

## Dataset and acceptance
`evals/datasets/business-scenarios.json` defines 20 input/expected-outcome cases. Cases requiring agents, approvals, connectors or recovery are **specifications for future tests**, not passing E2E results. `calculation-cases.json` contains 15 executable arithmetic cases; `procurement.json` contains four seeded orders for two tenants, with same supplier ID across tenants to exercise scoping.

Phase 0 exit: synthetic schema/fixtures, business rules, permission matrix, lifecycle/side-effect rules, threat model and 15–30 golden business cases available and consistency-checked. Phase 1 exit remains actual authenticated tenant-scoped application on the agreed foundation. No stage completion implies production readiness.
