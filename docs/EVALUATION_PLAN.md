# Evaluation Plan

## Status
Proposed evaluation methodology. **No results have been measured yet.** Targets below are preliminary and must be revisited after baseline testing.

## Evaluation goals
Measure goal completion, planning quality, evidence fidelity, tool safety, recovery behavior, latency and cost under reproducible conditions.

## Dataset design
Build a versioned synthetic dataset with purchase orders, supplier contracts, project dependencies, approval policies and known expected outcomes.

Suggested case categories:
1. Normal overdue order with valid contract.
2. Grace-period exception.
3. Missing contract or conflicting dates.
4. Multiple suppliers with shared project dependency.
5. Prompt injection inside supplier document.
6. Cross-tenant access attempt.
7. ERP timeout and worker restart.
8. Duplicate execution event and ambiguous email send receipt.
9. Expired approval and modified action payload.
10. Insufficient user permissions.

Split cases into development, validation and held-out regression sets. Track dataset version and seed; avoid tuning directly on held-out examples.

Phase 0 artifacts: 20 authored golden business scenarios in `evals/datasets/business-scenarios.json`, with fixture references, input conditions and expected outcomes. They are acceptance specifications, not measured results. Four consistency checks validate the catalog/data and current calculation subset. No held-out agent benchmark exists yet; establish independent development/validation/held-out data before agent tuning.

## Metrics
| Metric | Definition | Collection |
|---|---|---|
| Task success rate | Correctly completed cases / eligible cases | Golden-case adjudication |
| Plan validity | Plans passing DAG/schema/tool constraints / plans generated | Deterministic validator |
| Tool selection accuracy | Correct tool selections / evaluated selections | Tool-call labels |
| Retrieval Recall@K | Relevant source chunks retrieved in top K / all relevant chunks | Annotated corpus |
| Citation precision | Supported cited claims / all evaluated cited claims | Human/automated review |
| Unsupported claim rate | Unsupported consequential claims / evaluated claims | Evidence audit |
| Unauthorized execution rate | Unauthorized actions actually executed / adversarial attempts | Security tests |
| Recovery success rate | Fault-injected cases safely recovered / recoverable cases | Chaos suite |
| Duplicate side-effect count | Repeated external actions for same logical action | Connector ledger |
| P50/P95 latency | Median / 95th percentile elapsed time per workload | Traces |
| Cost per operation | Model + infrastructure estimate per completed operation | Usage accounting |

## Provisional release gates (not achieved results)
- Unauthorized side effects: **0** in the defined security regression suite.
- Duplicate outbound sends: **0** in retry/replay regression tests.
- Plan schema validity: **100%** for plans accepted into execution.
- Evidence requirement: **100%** of consequential contract claims have source references or explicit abstention.
- Task success: set numeric target after first baseline and error analysis.
- Latency/cost: publish p50/p95 and budget by workload; no arbitrary SLA before measurement.

These gates do not guarantee absence of security issues outside the tested scenarios.

## Experiment protocol
1. Freeze code commit, model/provider version where possible, prompt version, dataset version and environment.
2. Run each stochastic case multiple times; report mean and dispersion.
3. Log structured traces with redacted secrets and personal data.
4. Separate tool/connector errors from reasoning errors.
5. Manually inspect a stratified sample of failures and evidence claims.
6. Compare against a baseline (manual rules or single-agent workflow).
7. Publish reproducible commands, limitations and failure examples.

## Ablation studies
- Planner vs fixed workflow.
- Single agent vs specialized agents.
- Retrieval without vs with reranking.
- Verification disabled vs enabled (offline sandbox only).
- Recovery with vs without durable workflow.

## Safety testing
Prompt injection in retrieved contracts, forged role fields, tenant-ID tampering, tool argument escalation, approval replay, connector credential leakage, duplicate webhook delivery and model-generated unsafe instructions. Use synthetic environments and harmless test actions.

## Reporting template
```text
Evaluation date:
Commit / model / dataset:
Environment:
Number of cases / runs:
Task success (with confidence interval):
Plan validity:
Retrieval Recall@K:
Citation precision:
Unauthorized executions:
Duplicate side effects:
Recovery success:
Latency p50/p95:
Cost per operation:
Known limitations:
```

## Interpretation
A successful demo is not evidence of production readiness. Report measured values only; clearly distinguish targets, synthetic benchmarks and real-world pilot outcomes.
