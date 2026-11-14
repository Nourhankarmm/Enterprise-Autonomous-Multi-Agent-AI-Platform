# Technology Stack and Decisions

## Proposed baseline
| Concern | Selection | Rationale | Trade-off |
|---|---|---|---|
| Web UI | Next.js + TypeScript | Rich operational UI and typed client | More frontend build complexity |
| UI graph | React Flow | Visual operation/agent graph | Not necessary for initial API prototype |
| Backend | Python + FastAPI | Async APIs, typed schemas, AI ecosystem | Requires careful worker separation |
| Agent orchestration | LangGraph | Structured state and conditional agent flow | Overlaps with workflow concepts if poorly scoped |
| Durable workflow | Temporal | Long-running state, timers, retries, signals | Operational complexity |
| Relational DB | PostgreSQL | Transactions, tenant data and audit projections | Requires migrations and tuning |
| Vector retrieval | pgvector | Keep vector search near transactional metadata | Evaluate scale/latency limits |
| Cache | Redis (optional) | Short-lived cache/rate-limit support | Avoid as source of truth |
| Object storage | S3-compatible | Versioned contract/artifact storage | ACL and lifecycle controls needed |
| LLM provider | Provider abstraction (OpenAI initially) | Flexible experimentation | Cost and model drift |
| Telemetry | OpenTelemetry + Langfuse | End-to-end traces and LLM observability | Sensitive trace redaction required |
| Metrics | Prometheus + Grafana (later) | Service health and dashboards | Extra maintenance |
| Containers | Docker Compose initially | Reproducible local development | Not production orchestration |
| Deployment | Kubernetes later, if justified | Scaling and isolation options | Significant complexity |

## Decision records
### ADR-001: Goal-first product
Choose autonomous goal-to-plan experience rather than a general no-code automation builder for MVP. Rationale: deeper demonstration of planning, safety and verification with smaller surface area.

### ADR-002: Policy engine outside LLM
LLM output is advisory. Tool gateway and policy engine enforce privileges and approval constraints deterministically.

### ADR-003: Temporal owns long-running lifecycle
Avoid competing lifecycle state machines. LangGraph operates as bounded reasoning activity within Temporal-managed operation.

### ADR-004: Mock ERP before real integrations
Use deterministic fixtures to build repeatable tests and demos. Add a real adapter only after security and contract tests are in place.

### ADR-005: PostgreSQL + pgvector first
Prefer operational simplicity for MVP; benchmark retrieval before adding dedicated vector infrastructure.

## Deferred choices
Cloud provider, identity provider, secrets manager, queue broker, dedicated policy engine, enterprise SSO and multi-region deployment are not finalized. Select based on pilot requirements and measurements.

## Engineering standards
Python typing and formatting, TypeScript strict mode, Pydantic schemas, OpenAPI, automated tests, dependency scanning, migration review, structured logs and documented architecture decisions.
