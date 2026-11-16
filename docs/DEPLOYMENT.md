# Deployment and recovery

This release is a bounded synthetic demonstration. The supplied Compose stack is loopback-only, uses a local password-authentication stub and a Temporal development server, and sends only to the sandbox mailbox. It is not an internet-facing production deployment.

## Reproduce

Use Python 3.12+, Docker Compose v2 and Node 22 for JavaScript checks. Copy `.env.example` to `.env`, set a local PostgreSQL password, then run:

```sh
pip install -c requirements.lock.txt '.[test]'
docker compose up --build -d --wait
python scripts/compose_smoke.py
python scripts/durable_smoke.py
python scripts/verify_compose_restore.py
python scripts/run_regressions.py
python scripts/run_evaluation.py
```

The API binds to `127.0.0.1:8000`; databases and Temporal are private to the Compose network. Migrations run before seed/API/worker. Revision `0007` adds shared admission windows; `0008` adds document kinds and independently reviewed amendments. Back up before upgrades; do not automatically downgrade a database containing new production records. SQLite is a local/test option, not the deployment concurrency baseline. CI separately runs regressions against PostgreSQL 17 and the disposable Compose stack.

## Admission and HTTP controls

Limits are database-backed fixed windows shared across API processes. Defaults: peer 600/minute; login peer 20/300 seconds; normalized account 10/300 seconds; authenticated user 180/minute; planning 6/minute; upload 6/minute; search 30/minute. Settings are `API_PEER_LIMIT`, `LOGIN_PEER_LIMIT`, `LOGIN_ACCOUNT_LIMIT`, `AUTH_USER_LIMIT`, `PLANNER_USER_LIMIT`, `UPLOAD_USER_LIMIT`, `SEARCH_USER_LIMIT` (see `platform_api/security.py` and `main.py` for authoritative names). A rejected request returns 429 and `Retry-After`; database admission failures return generic 503. Windows contain hashed keys, not raw accounts or tokens; hashes are still sensitive metadata. Old buckets are periodically removed after one hour. Boundary bursts and database/connection exhaustion still require infrastructure controls. Injected test identities bypass limits unless `RATE_LIMIT_ENABLED=1`; ordinary application sessions do not.

JSON bodies are capped at 64 KiB even without a truthful Content-Length. PDF uploads retain the separate bounded multipart limit. Responses carry no-store, CSP, frame denial, content-type protection and server-generated request IDs. Unexpected errors do not expose exception messages. Set `ALLOWED_HOSTS` to exact comma-separated names. Forwarded headers are ignored by default, including client-supplied HTTPS claims.

`DEPLOYMENT_MODE=restricted` rejects startup without PostgreSQL, explicit host names, `REQUIRE_HTTPS=1`, enabled limits and disabled development tokens. It disables OpenAPI/docs. This guard is not production certification. With TLS terminated at a reverse proxy, an operator must explicitly override the Uvicorn command to trust only that proxy's address via `--proxy-headers --forwarded-allow-ips=<proxy-address>`, block direct API access and configure health probes accordingly. Never trust all forwarded sources. The supplied demo does not enable this topology automatically.

## Secrets and containers

`OPENAI_API_KEY_FILE` and `GEMINI_API_KEY_FILE` accept a mounted secret file; explicit environment values take precedence. Files are bounded to 4 KiB and are not copied into the environment. Configure the chosen provider/model separately. Mount files read-only using a Compose override with `secrets` and the corresponding `_FILE` setting. Do not commit keys or print container environments. See [Docker secrets](https://docs.docker.com/compose/how-tos/use-secrets/).

Application containers run with a read-only root, no capabilities, no privilege escalation, a 64 MiB noexec temporary filesystem, 128-process limit, 768 MiB memory limit and one CPU. These bounds are demo defaults to tune with measured workloads. PostgreSQL and Temporal require their writable data volumes. The parser child is not a complete OS sandbox. Resource settings follow the [Compose service reference](https://docs.docker.com/reference/compose-file/services/); HTTP admission follows [OWASP REST guidance](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).

## Backup and restore

Use `pg_dump -Fc` from the database container with access-controlled binary output; never put dumps in CI artifacts or Git. Preserve the PostgreSQL and Temporal volumes together during an operator-approved maintenance window. Encrypt backups, define retention and test restoration regularly. `scripts/verify_compose_restore.py` restores a custom-format dump into a randomly named **new** database, compares domain counts and PDF bytes/checksums, then removes only that temporary database. It never overwrites the source. This verifies database restore, not cross-service point-in-time consistency or disaster-recovery RTO/RPO.

Before any public pilot: implement OIDC/session lifecycle, least-privilege database roles (the demo role owns tables), production Temporal with authentication/TLS/backups, trusted TLS ingress, incident/retention policy, independent penetration testing and a real provider's reconciliation contract. Legal signature validation, semantic retrieval and real email remain unavailable. Optional local OCR is described in PHASE_7_REVIEW.md, including its strict limits and human-comparison gate. Do not expose this demo publicly as a substitute for these gates.
