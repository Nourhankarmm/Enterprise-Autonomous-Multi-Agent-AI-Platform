# Phase 6 review

This phase adds reproducible offline evaluation, shared database-backed admission limits, HTTP hardening, file-mounted provider secrets, bounded application containers, safe failures, migration 0007, and a real subprocess worker-crash recovery test. No real email is sent.

## Measurement scope

Run `python scripts/run_evaluation.py` for a temporary SQLite / in-process ASGI measurement using the deterministic demo planner. The report records source commit/dirty state, prompt and dataset hashes, platform, repetitions and all check outcomes. It includes 15 development arithmetic cases plus 40 frozen synthetic regression cases, four planner goals repeated five times, five document-grounded executions, exact PDF citation-span checks, and two positive/one negative lexical queries. The new dataset separates ten validation and thirty held-out regression labels; it is authored synthetic data, not an independent external sample.

The initial working-tree run passed 90/90 checks: 55/55 unique calculations, 20/20 valid demo plans, 5/5 expected graph results, 20/20 citation spans, and recall@5 of 1.0 on the two curated queries. Read-only evaluation produced zero sandbox messages. These are narrow deterministic checks, not real-model accuracy, legal correctness, broad retrieval quality, a statistical success estimate or a production SLA. Published JSON and CI artifacts provide actual environment/timing details. Offline model cost is zero; infrastructure cost is unknown. No live provider benchmark or ablation study was performed.

Run `python scripts/run_regressions.py` for named test results. Real Temporal integration is separate: configure `TEST_TEMPORAL_ADDRESS` and run `python -m unittest discover -s tests -p test_temporal_integration.py -v`. It kills a separate worker with exit 73 after the sandbox accepts a message but before the receipt is projected; a replacement worker must reconcile to COMPLETED with one message. It also checks replay, waits, expiry and cancellation. This proves the defined sandbox contract only, not arbitrary external-provider exactly-once delivery.

## Business specification coverage

The twenty Phase 0 scenarios remain acceptance specifications. Existing tests exercise their constituent controls, but there is no claim of twenty complete business E2E cases. B01 requires an explicit manual draft; B04 detects date conflicts without verifying legal signatures; B05 verified amendments and B06 aggregate inventory/project analysis are deferred. B17 has safe unknown-outcome handling but no production connector or manual reconciliation UI. B19 supports one logical action per workflow rather than the entire specified multi-action journey. Remaining approval, tenant, ACL, hostile-document, retry and cancellation behaviors are covered by the Phase 1–6 regressions and real Temporal test within their synthetic scope.

## Release boundary

See [deployment and recovery](DEPLOYMENT.md). Local authentication, Temporal development mode, sandbox mail, lexical retrieval, conservative reviewed PDF extraction and synthetic ERP remain explicit boundaries. Limits are fixed-window admission controls, not a complete denial-of-service defense. A dependency audit is a dated advisory lookup, not proof that dependencies are vulnerability-free. CI publishes per-commit JSON for regression/evaluation/recovery evidence; never publish keys, database dumps or raw provider errors.
