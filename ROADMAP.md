# Implementation Roadmap

## Planning assumptions
Solo developer; part-time or full-time pace changes duration. Estimate: **21–28 weeks** total. This is a planning estimate, not a delivery promise.

## Phase 0 — Scope and test fixtures (1 week, overlaps planning)
**Deliver:** synthetic ERP data schema, contract examples, 15–30 representative business scenarios, initial threat model.
**Exit gate:** inputs and expected outputs reviewed; no production data required.

## Phase 1 — Platform foundation (3–4 weeks)
**Deliver:** monorepo scaffold, FastAPI, web shell, PostgreSQL migrations, OIDC/auth stub, tenant-aware access tests, mock ERP read connector, Docker Compose, CI.
**Exit gate:** user can authenticate and retrieve only tenant-scoped purchase orders.

## Phase 2 — Agent planning and execution (4–5 weeks)
**Deliver:** structured planner, LangGraph agent graph, typed tool gateway, plan validator, task state persistence, baseline agent tests.
**Exit gate:** plan DAG passes schema/security checks and produces traceable results on synthetic tasks.

## Phase 3 — Documents and risk intelligence (3–4 weeks)
**Deliver:** ingestion pipeline, ACL-filtered retrieval, contract citations, deterministic delay calculator, risk explanation and verification.
**Exit gate:** reference cases produce supported findings; missing evidence is surfaced explicitly.

## Phase 4 — Durable workflow and approvals (4–5 weeks)
**Deliver:** Temporal operation lifecycle, approval inbox, action hashing, email draft/send adapter, idempotency ledger, recovery tests.
**Exit gate:** replay/restart cannot create duplicate sends; unapproved actions are blocked.

## Phase 5 — Operations UI and telemetry (3–4 weeks)
**Deliver:** goal composer, plan review, execution timeline, agent trace panel, evidence viewer, approval queue, cost dashboard.
**Exit gate:** complete demo can be performed through UI with auditability.

## Phase 6 — Evaluation and production hardening (4–6 weeks)
**Deliver:** reproducible benchmarks, failure-injection tests, security regression suite, rate limiting, deployment guide, threat model updates, demo video and GitHub release notes.
**Exit gate:** documented metrics, known limitations, and reproducible setup; no unsubstantiated production-readiness claim.

## Milestone checklist
- [ ] M0: Synthetic dataset and golden cases
- [ ] M1: Tenant-scoped API and mock ERP
- [ ] M2: Validated agent plans and execution traces
- [ ] M3: Citation-backed procurement analysis
- [ ] M4: Durable approvals and safe side effects
- [ ] M5: Usable operations dashboard
- [ ] M6: Published evaluation report and reproducible demo

## Priority rules
P0: tenancy, tool policy, structured plans, evidence, approvals, idempotency, end-to-end demo.

P1: scheduled runs, advanced retrieval, richer dashboard, configurable policies.

P2: generalized workflow builder, multi-ERP marketplace, enterprise billing, Kubernetes autoscaling.

## Definition of done
Feature implemented; tests passing; security controls reviewed; documentation updated; tracing and error states exposed; limitations recorded.

## Risks to schedule
Temporal setup, integration edge cases, document parsing accuracy, model nondeterminism, policy design and frontend scope. Cut P1/P2 features before compromising P0 safety.
