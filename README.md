# Enterprise Autonomous Multi-Agent AI Platform

Phase 0 planning/data and Phase 1 application foundation are implemented. Phase 1 uses a **local authentication stub**, not OIDC/SSO. Agents, contract ingestion, Temporal, approvals and sending belong to later phases.

See [Phase 1 review](docs/PHASE_1_REVIEW.md), [roadmap](docs/ROADMAP.md), and [Phase 0 rules](docs/PHASE_0_SPEC.md).

## Local PowerShell setup

Python 3.12+ required. From the repository root:

```powershell
python -m venv .venv
python -m pip --python .\.venv\Scripts\python.exe install -c requirements.lock.txt -e '.[test]'
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m platform_api.manage seed
.\.venv\Scripts\python.exe -m platform_api.manage create-user --username alpha-manager --tenant alpha --role manager
.\.venv\Scripts\python.exe -m platform_api.manage create-user --username beta-analyst --tenant beta --role analyst
.\.venv\Scripts\python.exe -m uvicorn platform_api.main:app --host 127.0.0.1 --port 8000
```

Choose passwords of at least 12 characters at the hidden prompt. There are no shipped default accounts/passwords. Open http://127.0.0.1:8000 and sign in. Alpha sees PO-A1/A2/A3; beta sees only PO-B1. Refreshing the browser requires sign-in again: the bearer token stays in memory, never localStorage. Server sessions last one hour and logout revokes them.

This local path uses **SQLite `.data/phase1.db`** for convenience. The old `.data/platform.db` is preserved; its legacy JSON schema is not automatically converted. Do not point the new server at it. On this machine, the base interpreter is `C:\Users\DELL\AppData\Local\Python\pythoncore-3.14-64\python.exe` if `python` is unavailable.

## PostgreSQL with Docker Compose

Docker Engine and Compose are required. Copy `.env.example` to `.env` and set `POSTGRES_PASSWORD` to a random alphanumeric value of at least 32 characters. It is a local development database credential; do not reuse a real credential.

```powershell
Copy-Item .env.example .env
# Set POSTGRES_PASSWORD in .env before continuing.
docker compose up --build -d
docker compose exec api python -m platform_api.manage create-user --username alpha-manager --tenant alpha --role manager
docker compose exec api python -m platform_api.manage create-user --username beta-analyst --tenant beta --role analyst
```

Compose waits for PostgreSQL health, runs Alembic migrations, seeds synthetic data, then starts the app. Only the API port is published, on loopback. PostgreSQL data is retained in `postgres_data`. `docker compose down` stops services without deleting that volume.

For an existing PostgreSQL server set `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DB` in the process environment (URL-encode credentials), then run Alembic, seed, create-user and uvicorn as above. PostgreSQL schema and fixtures are never automatically changed by API startup. Run migrations once as a deployment step before starting API workers.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check web/app.js
```

Expected: **23 unittest methods**, including 15 arithmetic subcases. To repeat Phase 1 acceptance against a dedicated, empty PostgreSQL test database:

```powershell
$env:TEST_DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:5432/DEDICATED_TEST_DB'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_phase_one.py -v
Remove-Item Env:TEST_DATABASE_URL
```

This runs nine methods, applies migrations twice, checks model/schema drift, seeds fixtures and writes test users/events. **Use a dedicated disposable database, never production.** The test database is retained for inspection. CI runs both SQLite and PostgreSQL 17 checks, builds/starts the full Compose stack and verifies login, tenant isolation, audit/session persistence across API restart and logout. It cleans up its disposable stack afterward. GitHub Actions execution passed, including a fresh Compose boot and restart acceptance: [verified run](https://github.com/Nourhankarmm/Enterprise-Autonomous-Multi-Agent-AI-Platform/actions/runs/43552653640).

## API

- `POST /v1/auth/login`: JSON `username`, `password`; returns one-hour bearer session.
- `POST /v1/auth/logout`: revoke the current session.
- `GET /v1/me`: server-resolved company, role and subject.
- `GET /v1/purchase-orders` and `/{id}`: manager/analyst, scoped to their company.
- `GET /v1/procurement/assessment?as_of=2026-11-14`: deterministic assessment.
- `GET /v1/audit-events`: latest 100 redacted tenant events for manager/auditor.
- `GET /health`: checks database connectivity; `/docs`: interactive API reference.

Pass `Authorization: Bearer SESSION_TOKEN`. Company headers/query parameters cannot change the authenticated tenant. Unknown and other-tenant order IDs both return 404. Passwords use salted PBKDF2-SHA256; session tokens are stored hashed. User activation and role are checked on every request. Legacy environment development tokens require explicit `ENABLE_DEV_TOKENS=1`; normal login does not use them.

## Limits

Synthetic local demonstration only. Local auth has no enterprise SSO, MFA, password recovery, brute-force rate limiting or identity lifecycle integration. Use loopback for the demo; remote deployment requires HTTPS and identity hardening. Audit records are durable, not tamper-proof. Composite foreign keys and application query scoping are implemented; PostgreSQL RLS is not. The UI is a static Arabic shell served by FastAPI; Next.js is deferred until the operations UI phase. Compose configuration and GitHub Actions syntax passed official CLI/actionlint checks. Docker Engine is unavailable on this Windows machine; actual image builds, fresh Compose startup, HTTP acceptance and cleanup passed on the GitHub-hosted Linux runner. Actual PostgreSQL acceptance was run independently using a portable PostgreSQL 17 server.
