# Phase 5 — operations dashboard and local telemetry

This phase turns the existing procurement, planning, documents and approval surfaces into one navigable Arabic workspace. The existing FastAPI/static application is retained; it does not introduce a second frontend runtime or replace the Phase 4 workflow protocol.

## Delivered behavior

- Role-aware overview, goal/plan, workflow/approval, document/evidence, usage/cost and purchase-order sections. The owner sees their analyses/workflows; an approver sees assigned cases and explicitly readable evidence, without procurement or planner-cost access.
- A bounded overview with state metrics, searchable/filterable records and direct detail actions. Counts describe the permitted window of up to 100 analyses and 100 workflows, not company-wide totals. Refreshed analysis snapshots are labelled as saved analyses, not extra model calls.
- One reviewed execution button uses Temporal when configured and the bounded local runner otherwise. Linked operations expose their workflow and disable conflicting legacy mutation controls. Evidence and the current workflow analysis snapshot are reachable from the review surface.
- Recorded tool-attempt timings, dependency/state/retry/error metadata, output counts and timestamped execution events. This is a business/tool trace, not raw private model reasoning. Missing timing endpoints are unknown, not invented measurements.
- Owner/assigned-case workflow timeline including proposal versions, decisions, state transitions and receipts. Arbitrary audit JSON and actor identities are not returned; ordinary application audit records are not described as immutable.
- Foreground-only five-second refresh for overview and selected details, with no overlapping automatic polls. Forms are preserved during refresh. If a proposal changes, approval controls are disabled until an explicit detail refresh and review. Logout and access errors clear affected evidence/review data.
- Responsive RTL layout, labelled forms, keyboard-accessible navigation, meaningful empty/loading/error states and visible update timestamps. Tables scroll within their containers on small screens.

## Telemetry and cost semantics

Migration 0006 adds local `planner_calls`. Each new planning attempt records provider/model, success/failure/rejection, elapsed milliseconds, a bounded allowlist of provider-reported input/output token counts, and an optional captured price estimate. Prompt text, documents, raw provider error bodies, keys and private thinking are never stored in this telemetry table. Replanning records a new call; document refreshes reuse the confirmed plan and do not duplicate model usage.

The owner-only report covers up to 1,000 permitted calls from the last 30 days **since telemetry was enabled**. It does not reconstruct historical model calls from old operation snapshots. Unknown usage and missing price coverage remain explicit; the known subtotal is distinct from the total estimate. Offline demo calls are explicitly zero-token, zero-model-cost calls; that does not imply free usage for a live provider.

No provider prices are assumed. An operator may configure `MODEL_PRICING_JSON`, keyed by `provider/model`, with `currency: USD`, nonnegative `input_per_million` and `output_per_million`, an `as_of` date and an HTTPS `source` URL. Rates and source are captured with the call so later configuration changes do not rewrite old estimates. Invalid/missing/future-dated settings leave costs unavailable. URLs containing credentials/query strings are rejected. These are standard input/output rate estimates, not provider invoices: cached-token discounts, account/free-tier rules, taxes, negotiated rates and infrastructure costs are outside the calculation. Configuration is local and read-only to app users.

Example format only (fictional test rates, **not actual provider pricing**):

```json
{"gemini/example-model":{"currency":"USD","input_per_million":"2","output_per_million":"4","as_of":"2026-01-01","source":"https://example.invalid/pricing"}}
```

## API and verification

- `GET /v1/dashboard`: permitted bounded records, state counts and snapshot timestamp.
- `GET /v1/operations/{id}/trace`: owner-scoped tool timing/trace metadata after document-access checks.
- `GET /v1/workflows/{id}/timeline`: owner/assigned-approver timeline with live case/document checks.
- `GET /v1/telemetry/planner?operation_id=...`: own call records and coverage-aware estimates; optional operation scope is authorization checked.

Phase 5 tests cover tenant/owner/assignment filtering, revoked roles and PDF access, sanitized traces/audits, attempt timings, captured decimal prices, invalid rates, unknown usage, failed/rejected planning, replans, nonduplicated document refresh usage and empty historical coverage. CI repeats these against PostgreSQL and extends Compose acceptance with dashboards, telemetry persistence, workflow timelines and approver restrictions. Final branch/PR checks are authoritative for executed validation.

The implementation at `b5cfe0d` passed 137 ordinary tests, all phase suites against PostgreSQL, Compose HTTP/document/telemetry persistence acceptance, killed-worker recovery and real Temporal replay in [run 43742047363](https://github.com/Nourhankarmm/Enterprise-Autonomous-Multi-Agent-AI-Platform/actions/runs/43742047363). The optional real Temporal integration method is skipped in ordinary discovery and executed separately in Compose. Final UI/session refinements are checked on the final [PR #5](https://github.com/Nourhankarmm/Enterprise-Autonomous-Multi-Agent-AI-Platform/pull/5) head.

Browser acceptance completed PDF sample upload, page-2 review/publication and sharing, exact dated plan review, background execution, tool traces and evidence navigation, a frozen follow-up, then independent assigned approval of revision 2 and one sandbox receipt. Changing the proposal while the approver viewed revision 1 showed a warning and disabled both decision controls until explicit refresh. Search/type filters, saved results after re-login, live workflow updates and the explicitly zero-cost offline usage report were verified. At a 390-pixel viewport the page was 375 pixels wide (excluding the scrollbar) and the wide cost table scrolled inside its container. Logout cleared dashboard/usage rows, trace cards and displayed evidence. Proof: `evals/reports/phase5-ui-acceptance.json`. QA used a separate tenant; its users were disabled and sessions revoked afterwards. This is a local demo-planner acceptance, not a new live-model or invoice verification. A long-page browser screenshot artifact was found to shift RTL content; the final dashboard image was captured directly from the actual viewport and visually checked against DOM geometry.

## Remaining boundaries

This is a synthetic local application, not a production observability platform. It has bounded windows and polling rather than streaming or unlimited reporting, no external tracing exporter, no general admin/company cost analytics, no provider invoice synchronization and no verified SLA/performance benchmark. Production identity/rate limits, retention, stronger audit storage, deployment and formal benchmark/security hardening remain Phase 6. OCR, semantic-vector retrieval and real email delivery remain outside the implemented demo.
