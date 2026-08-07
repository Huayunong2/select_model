# Evidence sources and panel rules

Use this reference when choosing evidence, constructing envelopes, or interpreting evidence confidence.

## Core rule

Public sources are separate measuring instruments. Never arithmetic-average their raw scores and never call the aggregate a task-success probability.

The v4 router:

1. validates each provenance-rich Evidence Envelope;
2. rejects unknown, stale, future-dated, or mismatched evidence;
3. groups observations into comparable panels;
4. ranks candidates inside each panel;
5. aggregates source-internal rank signals with task relevance and reliability weights;
6. applies source-regret guards and source-removal stability checks.

## Comparable panel

A panel is identified by:

```text
source_id
+ metric.name
+ metric.version
+ harness
+ snapshot_id
+ metric.higher_is_better
```

Only observations in the same panel may be compared directly. Do not merge different harnesses or metric versions merely because values share a 0–100 range.

## Envelope requirements

Prefer complete records containing:

```text
source_id, observed_at,
subject.model, subject.effort, subject.snapshot,
metric.name, metric.value, metric.higher_is_better, metric.version,
match, sample_size, ci_half_width,
harness, snapshot_id, source_url, raw_sha256
```

Safe defaults:

- missing/invalid `match` becomes `proxy`;
- missing time, sample, uncertainty, or provenance reduces weight;
- strict mode rejects missing time, sample, snapshot ID, source URL, or hash;
- unknown sources are rejected;
- records beyond the configured TTL are rejected;
- exact evidence requires matching model and effort.

## Registered source roles

The exact policies are in `config/sources.json`; verify current source behavior before use.

### CodexRadar

Use for short-horizon model+effort health when the displayed row and harness match. Preserve sample count, freshness, trend, and raw provenance. Benchmark cost/latency are proxies unless they match the user's task.

### Arena Agent / Code / WebDev / Text

Choose the task-relevant board. Prefer published percentile or rank/total and preserve board version and sample information. Do not convert raw Elo/rating into a fabricated universal ability score.

### Artificial Analysis

Use the task-relevant index as an independent baseline. Mark family-level or harness-mismatched observations as `family` or `proxy`, not `exact`.

### SWE-bench-Live

Use only for software-engineering tasks and comparable agent/harness configurations. Resolved percentage is source-native evidence, not a universal model score.

## Confidence limits

- One independent source cannot produce high evidence confidence.
- A source with several panels still counts as one independent source.
- Missing provenance cannot be offset merely by a high headline score.
- Freshness and sample size affect weight but do not make a proxy exact.
- Public evidence remains a prior; only real personal outcomes can calibrate `p_success`.

## Adding a source

Before adding a source:

1. document the construct it measures;
2. define accepted metric names and direction;
3. define TTL and freshness half-life;
4. define sample-scale behavior;
5. define task-family relevance;
6. provide official HTTPS URLs;
7. add collector/adapter provenance;
8. add tests for missing metadata, stale data, mismatched panels, and unknown metrics.
