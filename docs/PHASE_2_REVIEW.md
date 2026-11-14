# Phase 2 — planning and agent execution review

Date: 14 November 2026. This phase adds bounded procurement planning and execution on the Phase 1 foundation. It does not introduce Temporal, document ingestion, approvals or external actions.

## Implemented

| Deliverable | Implementation |
|---|---|
| Structured planner | Strict Pydantic proposal/task/filter schemas; optional live OpenAI Responses structured output; separately labelled offline demo adapter |
| Plan validation | Unknown fields/tools, identity/tenant claims, duplicate tasks/dependencies, missing dependencies, cycles, skipped verification and expanded budgets rejected |
| Review gate | Server-owned plan version, canonical SHA-256 hash bound to tenant/requester/date/tasks; exact confirmation required; stale/replayed confirmation rejected |
| LangGraph runtime | Compiled five-capability task DAG: purchase order retrieval, synthetic contract lookup, deterministic assessment, verification, report |
| Typed gateway | Read-only allowlist; current server identity supplies tenant; task arguments use validated filters or successful predecessor snapshots |
| Persistence | Alembic `0002`: tenant-owned operations, task outputs/attempts and public execution events; operation access restricted to its requesting user |
| Recovery boundaries | Atomic execution claim, 10-second lease, explicit read-only resume of unfinished tasks; successful task snapshots reused; cancelled/terminal operations cannot restart |
| Budgets | At most two revisions, three planner calls total; one model request per planning call, no SDK retries, 20-second model request timeout and 2,500 output-token ceiling; 12 total tool attempts, max 3 per task, 30-second operation wall-clock execution deadline |
| Verification | Findings recomputed against saved order/contract snapshots; mismatches stop report creation; missing contract yields unknown contractual priority and WAITING_DOCUMENT with supported partial findings |
| UI | Arabic goal composer, visible demo/live mode, saved operations, plan/dependency/scope review, confirmation, revision/cancel/resume, results/evidence and execution history |
| Secure local launch | `scripts/run_local.py --model MODEL_ID --port 8001` prompts for a key without storing it in repository files; model selection is explicit |

The live planner sends the goal, requested date, tool descriptions and schema to OpenAI. It receives no tenant credentials, records, contract contents or execution privileges. Numerical results and report contents come from the local tools. Provider refusals/incomplete responses/errors do not silently switch to the demo planner. SDK errors are redacted.

The v1 plan shape is intentionally narrow: exactly one of each of the five approved capabilities, with configurable task IDs, validated dependencies and order/supplier/project filters. This is not a generalized autonomous workflow builder. The offline adapter accepts only the published sample goal (and its two documented English equivalents); other goals request clarification instead of ignoring their scope.

## Verification

- 50 unittest methods: existing 23 plus 27 Phase 2 methods. The original 15 arithmetic subcases remain included.
- Phase 2 tests include malicious tool/tenant/budget proposals, DAG errors, exact confirmation/revision/replay controls, tenant and owner isolation, transient/permanent errors, concurrent confirmation, timeout, cancellation during a read, account revocation, tampered findings, trace pagination and crash/resume without repeating successful tasks.
- Real OpenAI SDK request serialization and structured response parsing are tested using an in-memory HTTP transport. No test uses a real API key or makes a paid model request.
- Migration tests adopt only the exact known Phase 1 SQLite bootstrap schema while preserving users/hashes, and refuse unknown schema differences. The existing local demo database was upgraded without altering accounts.
- Browser: sign in, create the sample plan with zero attempts, confirm, observe all five successful tasks, evidence-linked findings and missing PO-A3 contract, then sign out. Snapshot: `.data/phase2-result.jpg`.
- CI includes the Phase 2 suite against PostgreSQL and full Compose acceptance. The HTTP container check creates/confirms a plan and verifies persisted task/results after API restart.

Live-model acceptance remains pending local OPENAI_API_KEY/OPENAI_MODEL configuration and an actual successful model-generated operation. Offline and mocked provider tests do not establish live provider availability or model reasoning quality. The initial implementation CI run passed on GitHub, including PostgreSQL and the extended Compose smoke check: https://github.com/Nourhankarmm/Enterprise-Autonomous-Multi-Agent-AI-Platform/actions/runs/43597341673. Later small hardening/documentation changes are covered by the current branch checks.

## Runtime and security limits

Execution is synchronous inside the API request. Database/task snapshots are persisted, but this is not a durable background scheduler. A crash requires explicit resume after the short lease expires and before the original execution deadline. An elapsed deadline fails the operation, and cancellation stops remaining read-only tasks. No claim of arbitrary long-running recovery is made; Temporal remains Phase 4.

The wall-clock budget is checked at every task boundary. Database connections/queries have five-second limits; Python code is not forcibly killed mid-instruction. There are no external tool calls in this runtime. Successful predecessors are reused from their recorded snapshot; freshness revalidation for external actions is not applicable because no external action exists.

Document lookup uses manually seeded synthetic terms only. PDF/RAG, document ACL retrieval, amendments/conflicts, project delay forecasting, sending/drafting email and real ERP writes remain outside this phase. WAITING_DOCUMENT is inspectable/cancellable; document upload/replanning from new evidence will be implemented in subsequent phases. Public traces contain task/status/budget metadata, not raw model chain of thought. LangSmith tracing is explicitly disabled for graph execution.


## Opt-in live acceptance

After starting the live server on port 8001, run `python scripts/live_planner_smoke.py --username demo` and enter only the local login password at its hidden prompt. The test makes one model call through the server, verifies a supplier-S1-only plan before confirming it, checks the expected PO-A1 evidence/result, logs out, and writes non-secret evidence to `.data/live-planner-verification.json`. The API key stays in the server process. This test is deliberately excluded from CI and has not been claimed as passed.


Final core/hardening verification also passed on [CI run 43597574184](https://github.com/Nourhankarmm/Enterprise-Autonomous-Multi-Agent-AI-Platform/actions/runs/43597574184): 50 methods in the complete suite, nine Phase 1 PostgreSQL methods, 27 Phase 2 PostgreSQL methods, and the reviewed LangGraph operation through the fresh Compose stack. Live paid-model acceptance remains separate and pending.
