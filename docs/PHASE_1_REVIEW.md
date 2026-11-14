# Phase 1 exit review — 14 November 2026

## Delivered

| Deliverable | Implementation | Verification |
|---|---|---|
| Repository scaffold | `platform_api/`, `web/`, `migrations/`, `tests/`, `evals/`, CI/container configuration | Imports, API and UI smoke checks |
| FastAPI | Session, identity, procurement, assessment, audit and health routes | Existing 10 foundation methods retained |
| Relational PostgreSQL | SQLAlchemy schema: tenants, suppliers, projects, orders, contracts, users, sessions, audit | Real PostgreSQL 17.11 acceptance suite |
| Migrations | Frozen Alembic revision `0001`, explicit upgrade/downgrade | Repeated upgrade and schema consistency check |
| Auth stub | CLI-provisioned local users; salted PBKDF2-SHA256; hashed opaque one-hour sessions; revocation | Bad/forged/expired/revoked sessions rejected; disabled users and changed roles immediately enforced |
| Tenant ownership | Composite resource keys and supplier/project FKs; tenant from session only | Header/query spoofing, other-tenant IDs and invalid ownership relations tested |
| Mock ERP | Read-only connector seeded idempotently from synthetic fixtures | Same supplier ID yields company-specific contracts; no external ERP access |
| Web shell | Arabic RTL login, date-controlled assessment, metrics/table, error/empty states, logout | Browser login shows alpha's three orders, high-priority and missing-evidence counts; logout verified |
| Durable events | Tenant-scoped event table and restricted read route; no raw credentials recorded | Events survive app recreation; tenant scope and redaction checked |
| Compose | PostgreSQL 17 health -> migration -> seed -> non-root API; loopback API binding | Official Docker Compose CLI configuration validation passed; Docker Engine unavailable locally |
| CI | SQLite suite, PostgreSQL service acceptance, JavaScript syntax, Compose config and image build | actionlint validation passed; includes fresh Compose boot and HTTP/restart acceptance; remote execution pending authenticated push |

Exit gate achieved with the explicitly permitted **auth stub**: users can sign in and retrieve only their tenant's purchase orders. This is not an enterprise authentication or production-readiness claim.

## Validation evidence

- Full local suite: 23 unittest methods (10 foundation + 9 Phase 1 + 4 Phase 0), with 15 arithmetic cases nested in one method.
- Nine Phase 1 methods additionally run against a real portable PostgreSQL 17.11 server, bound only to 127.0.0.1 with password authentication.
- Alembic revision is applied twice and checked against SQLAlchemy metadata to catch schema drift.
- Browser: alpha login -> three alpha purchase orders -> expected HIGH/LOW/unknown results at 2026-11-14 -> logout -> login panel. Screenshot retained locally in `.data/phase1-dashboard.jpg`.
- Node syntax check for `web/app.js`; Python compilation checks.
- The installed Starlette currently warns about the httpx TestClient compatibility path; tests still pass. Migration to its newer client dependency can be evaluated independently.

## Architecture decisions and limits

The roadmap permits an OIDC/auth stub. This phase implements a real local password/session flow behind that stub, with no OIDC provider, SSO, MFA, password reset, rate limiting or production identity integration. Provision users explicitly with the CLI; no credentials are committed.

The proposed Next.js choice is deferred: the Phase 1 shell is static HTML/CSS/JS served from the same FastAPI origin, avoiding a second runtime for a read-only screen. Phase 5 can replace the shell with Next.js while keeping the API contract. No LangGraph, LLM, Temporal, RAG, email or approval execution is included in this phase.

SQLite remains an optional local/test adapter. PostgreSQL uses explicit migrations; tenant foreign keys prevent cross-company relationships. Query scoping enforces read isolation; PostgreSQL RLS and separate least-privilege migration/runtime database roles remain hardening work. Audit is append-only through the public API, but the database owner can modify it. An auditor's audit access remains a demo tenant-wide policy, with no contract contents exposed.

Legacy `.data/platform.db` is not destructively migrated. New local data uses `.data/phase1.db`; real data migration needs a reviewed import process. No production data is present.

Container and CI definitions are reviewable artifacts. Docker is unavailable locally, so the Compose lifecycle/image has not been executed here; CI must confirm it when pushed. PostgreSQL testing above did not depend on Docker.


## Container/CI verification follow-up

The official standalone Docker Compose CLI validates `compose.yaml` successfully. `actionlint` validates the GitHub Actions workflow with no findings. These are configuration checks, not evidence of a running container.

CI now boots the complete Compose stack from an empty volume and runs `scripts/compose_smoke.py`. It provisions random test-only accounts inside the disposable container, validates PostgreSQL readiness, tenant isolation, calculation results, audit/session persistence after restarting the API, and logout revocation. CI always collects container logs and removes only its own disposable stack/volumes. `API_BIND_PORT` allows its published loopback port to be isolated from the developer server.
