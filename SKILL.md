---
name: select-model
description: Choose and audit the lowest-total-cost model, reasoning effort, and execution path that remains sufficiently reliable for coding, research, analysis, writing, browser, agent, API, Codex, or long-running tasks. Use when comparing models or effort levels, checking current model health and pricing, routing high-volume workloads, reducing retries and spend, building a model dispatcher, or calibrating choices from personal execution history. Require current provenance-rich evidence, fail closed on high-risk eligibility gaps, keep benchmark signals separate from task-success probability, and execute only when required context is observable in the outgoing request or preserved by the host.
---

# Select Model

Use the deterministic v4 pipeline:

```text
current evidence -> task profile -> eligibility -> rank aggregation
-> task cost -> conservative route -> optional safe dispatch -> feedback
```

Keep the user-facing answer focused on the decision. Explain methodology only when requested.

## 1. Establish the task

Collect or infer:

- `task_type`, `risk`, `target_runtime`;
- required capabilities and context/output estimates;
- available task features: `reasoning, context, unfamiliarity, tools, browser, cross_file, test_quality, detectability, rollback, ambiguity, horizon`;
- expected token/tool usage, failure cost, and time value when useful;
- `repo_id` and `environment_id` when personal history is relevant.

Use coarse buckets when evidence is qualitative:

```text
very_low | low | medium | high | very_high
```

Attach confidence to uncertain features instead of inventing precise decimals. Missing information must lower task-vector confidence and narrow cost-saving downgrades.

Read `references/router-schema.md` for compact v4 input examples.

## 2. Obtain current evidence

Model availability, prices, capabilities, benchmarks, and product behavior can change. Verify current facts from primary or official sources before constructing a route.

For each observation, create a complete Evidence Envelope containing:

- source ID and observation time;
- exact model, effort, and snapshot;
- metric name, value, direction, version, and harness;
- `exact | family | proxy` match;
- sample size and uncertainty;
- source snapshot ID, HTTPS URL, and raw SHA-256.

Use registered sources only. Do not let missing metadata increase trust. Strict evidence requires time, sample size, and provenance. Stale evidence must be rejected after the source TTL.

Do not average heterogeneous raw benchmark values. The router ranks candidates only inside comparable source panels and combines source-internal rank signals with a weighted median and regret guards.

Read `references/sources.md` when selecting sources or constructing envelopes.

## 3. Construct and validate the route input

Prefer the versioned registries:

- `config/models.json` for model IDs, runtimes, efforts, context, capabilities, and pricing;
- `config/sources.json` for trust, TTL, accepted metrics, and task relevance.

Use a complete route input:

```bash
python scripts/select_model.py route \
  --input <route-input.json> \
  --history <history.jsonl> \
  --format markdown
```

High and critical risk default to strict eligibility. The router must reject unknown runtime, capability, context window, API effort, unregistered model, insufficient sources, or missing exact evidence when policy requires them. Never override a rejection merely because the candidate is cheap.

## 4. Use task-level total cost

Prefer cost evidence in this order:

1. current registry pricing plus task usage estimate;
2. current pricing plus similar personal usage;
3. explicit task-cost estimate;
4. benchmark cost proxy, labeled low confidence.

Include uncached input, cached input, cache writes, output/reasoning output, long-context multipliers, tools/search, latency, retries, human time, and failure cost when available. Missing cache-write pricing must produce unknown cost, not a low-price fallback.

## 5. Route conservatively

Accept a cheaper route only when it remains inside:

- runtime/capability/context/output eligibility;
- risk- and confidence-adjusted capability floor;
- exact-source regret guard;
- effort under-budget ceiling;
- minimum evidence-source and exact-evidence requirements.

Pareto-prune dominated candidates. Require material savings before accepting a lower-evidence route. Do not force every named tier to remain in the frontier.

Treat `routing_index` as a heuristic ordering signal, never as model intelligence or success probability.

## 6. Use personal history only from real outcomes

Record attempts with:

```bash
python scripts/select_model.py record \
  --route <route-result.json> \
  --response <api-response.json> \
  --outcome <outcome.json> \
  --history <history.jsonl> \
  --hash-identifiers
```

Track model snapshot, effort, product mode, token breakdown, latency, cost, tests, first-pass success, retries, fallback, final success, quality, and human-edit time.

Personal calibration must apply task-family/repo/environment similarity, model-snapshot weighting, and time decay. Emit `p_success` only when the returned `personal.available` is true. Before that, call the result provisional and do not use it as a calibrated probability.

## 7. Report the decision

Return:

- **Task:** family, pressure, risk, eligibility mode, and task-vector confidence;
- **Recommended:** exact model, effort, and product mode;
- **Why:** evidence coverage, regret protection, and task-cost source;
- **Plan:** on/off;
- **Fallback:** one stronger configuration when useful;
- **Personal calibration:** only when available;
- **Confidence:** route stability and evidence confidence;
- **Missing information:** the most informative feature when high-risk stability is low.

Do not expose the full machinery unless requested.

## 8. Execute only with preserved or observable context

Prefer a host-native action:

```text
execute_selected_model(model, effort, execution_context_ref)
```

A host can preserve repository state, files, tools, authenticated sessions, approvals, and conversation context. The Skill cannot change the model already running the current ChatGPT turn.

`scripts/select_model.py dispatch` creates a new Responses API request. Run a dry-run first:

```bash
python scripts/select_model.py dispatch \
  --route <route-result.json> \
  --context <context.json> \
  --dry-run
```

Count a capability as portable only when it is observable in the final request. `file_ids`, `portable_capabilities`, or host-state declarations do not create context by themselves. Missing high/critical-risk context requires both explicit context-loss and high-risk force flags.

Never send `OPENAI_API_KEY` to a custom endpoint. A custom endpoint requires explicit permission and a dedicated key environment variable.

Read `references/execution.md` for the execution contract.

## 9. Automate safely

Use the generic JSON collector for public HTTPS JSON or local snapshots:

```bash
python scripts/select_model.py evidence collect \
  --spec <collector-spec.json> \
  --output <snapshot.json> \
  --store <evidence.jsonl>
```

The collector must validate HTTPS, credentials, public DNS targets, every redirect, response size, envelope fields, and raw SHA-256. Do not add brittle HTML scraping to the core Skill.

Run diagnostics before production use or release:

```bash
python scripts/select_model.py doctor --strict
python scripts/release_check.py
```

For implementation details, use the Chinese `README.md` and the documents under `docs/`.
