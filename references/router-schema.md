# Router v4 compact schema

Use this reference when constructing route inputs or feedback records. Full details are in `docs/schema.zh-CN.md`.

## Route input

```json
{
  "schema_version": "4.0",
  "as_of": "2026-08-07T00:00:00Z",
  "task": {
    "task_type": "coding.debug.race_condition",
    "risk": "high",
    "target_runtime": "api",
    "repo_id": "payments-monorepo",
    "environment_id": "prod-like-linux",
    "estimated_context_tokens": 180000,
    "estimated_output_tokens": 18000,
    "required_capabilities": ["functions"],
    "features": {
      "reasoning": {"bucket": "very_high", "confidence": 0.9},
      "context": {"bucket": "high", "confidence": 0.8},
      "cross_file": "high",
      "test_quality": "low",
      "detectability": "low",
      "rollback": "medium",
      "ambiguity": "high",
      "horizon": "very_high"
    },
    "usage_estimate": {
      "input_tokens": 180000,
      "cached_input_tokens": 90000,
      "cache_write_tokens": 0,
      "output_tokens": 18000,
      "tool_cost": 0
    },
    "failure_cost": 25,
    "time_value_per_hour": 10
  },
  "candidates": [
    {"model": "gpt-5.6-sol", "effort": "medium"},
    {"model": "gpt-5.6-terra", "effort": "medium"},
    {"model": "gpt-5.6-luna", "effort": "medium"}
  ],
  "evidence": []
}
```

## Feature representation

Use one of:

```json
"reasoning": "high"
```

```json
"reasoning": {"bucket": "high", "confidence": 0.85}
```

```json
"reasoning": {"value": 0.72, "confidence": 0.85}
```

Prefer buckets over invented precision.

## Evidence Envelope

```json
{
  "schema_version": "1.0",
  "source_id": "codexradar",
  "observed_at": "2026-08-06T00:00:00Z",
  "subject": {
    "model": "gpt-5.6-luna",
    "effort": "medium",
    "snapshot": "gpt-5.6-luna"
  },
  "metric": {
    "name": "score",
    "value": 79,
    "higher_is_better": true,
    "version": "2026-08"
  },
  "match": "exact",
  "sample_size": 1000,
  "ci_half_width": 2,
  "harness": "published-harness",
  "snapshot_id": "source-snapshot-20260806",
  "source_url": "https://example.com/source",
  "raw_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

High/critical risk defaults to at least two independent sources and at least one exact model+effort record per candidate.

## API effort

For the API runtime, use only efforts allowed by the current model registry. Product modes such as a host-specific Ultra mode are separate from `reasoning.effort` and must use `product_mode` or host metadata.

## Attempt record

```json
{
  "schema_version": 2,
  "attempt_id": "unique-attempt-id",
  "recorded_at": "2026-08-07T00:00:00Z",
  "route_id": "route_...",
  "task": {
    "task_type": "coding.debug.race_condition",
    "risk": "high",
    "repo_id": "payments-monorepo",
    "environment_id": "prod-like-linux",
    "features": {"reasoning": "very_high", "cross_file": "high"}
  },
  "execution": {
    "model": "gpt-5.6-sol",
    "model_snapshot": "gpt-5.6-sol",
    "effort": "xhigh",
    "product_mode": "standard",
    "first_pass_success": true,
    "final_success": true,
    "tests_passed": true,
    "user_retry": false,
    "fallback_triggered": false,
    "latency_seconds": 210,
    "cost": 1.84,
    "quality_score": 0.95,
    "human_edit_minutes": 2,
    "usage": {
      "input_tokens": 170000,
      "cached_input_tokens": 80000,
      "cache_write_tokens": 0,
      "output_tokens": 16500,
      "tool_cost": 0
    }
  }
}
```

`output_tokens` must follow provider billing semantics and include billed reasoning output where applicable. Do not double-count it.
