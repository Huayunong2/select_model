# Select Model 数据 Schema

本文给出 4.x 公开 JSON 接口。实现使用轻量 Python 校验而非运行时第三方 JSON Schema 库；字段语义以本文、示例和测试共同为准。

## 目录

- [通用约定](#通用约定)
- [Route Input](#route-input)
- [Task](#task)
- [Candidate](#candidate)
- [Evidence Envelope](#evidence-envelope)
- [Route Result](#route-result)
- [Attempt / History](#attempt--history)
- [Context Manifest](#context-manifest)
- [Collector Spec](#collector-spec)
- [模型注册表](#模型注册表)
- [来源注册表](#来源注册表)
- [兼容性](#兼容性)

## 通用约定

- 编码：UTF-8；
- 时间：带时区的 ISO-8601，推荐 UTC `Z`；
- 金额：注册表指定币种，默认 USD；
- token：非负整数语义，解析时允许有限数值；
- 哈希：小写或大写十六进制 SHA-256，长度 64；
- ID：不应包含凭据、完整 prompt、私有代码或未脱敏个人信息；
- 未知字段：一般保留或忽略，核心安全字段仍需显式校验；
- `routing_index`：路由启发式，不是成功概率。

## Route Input

```json
{
  "schema_version": "4.0",
  "as_of": "2026-08-07T00:00:00Z",
  "task": {},
  "candidates": [],
  "evidence": []
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| `schema_version` | string | 建议 | 当前为 `4.0` |
| `as_of` | ISO-8601 | 建议 | 证据时效、价格时效、历史衰减的统一参考时间 |
| `task` | object | 是 | 任务描述 |
| `candidates` | array | 是 | 非空候选列表 |
| `evidence` | array/object | 是 | Evidence Envelope 列表或 `{records:[...]}` |

确定性重放时必须固定 `as_of`、注册表版本和 evidence snapshot。

## Task

```json
{
  "task_type": "coding.refactor.cross_file",
  "risk": "medium",
  "target_runtime": "api",
  "eligibility_mode": "auto",
  "repo_id": "example-monorepo",
  "environment_id": "linux-ci",
  "estimated_context_tokens": 100000,
  "estimated_output_tokens": 10000,
  "required_capabilities": ["functions"],
  "features": {},
  "usage_estimate": {},
  "failure_cost": 25,
  "time_value_per_hour": 10,
  "minimum_evidence_sources": 1,
  "require_exact_evidence": false
}
```

### 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_type` | string | 分层任务签名，如 `coding.debug.race_condition` |
| `risk` | enum | `low / medium / high / critical`；省略时 medium |
| `target_runtime` | string | `advisor / api / chatgpt / codex / work / atlas` 或已注册 runtime |

### 可选标识

| 字段 | 类型 | 说明 |
|---|---|---|
| `repo_id` | string | 用于个人历史相似性；可在落盘时哈希 |
| `environment_id` | string | OS、CI、工具链或执行环境签名；可哈希 |

### 资格与资源

| 字段 | 类型 | 说明 |
|---|---|---|
| `eligibility_mode` | enum | `auto / explore / balanced / strict` |
| `required_capabilities` | string[] | 候选必须具备的能力 |
| `estimated_context_tokens` | number | 预计上下文，超过窗口则拒绝 |
| `estimated_output_tokens` | number | 预计最大输出，超过限制则拒绝 |
| `minimum_evidence_sources` | integer | 覆盖风险默认值 |
| `require_exact_evidence` | boolean | 是否要求 exact model+effort 证据 |

### 任务特征

支持三种写法：

```json
"reasoning": "high"
```

```json
"reasoning": 0.72
```

```json
"reasoning": {"bucket": "high", "confidence": 0.8}
```

```json
"reasoning": {"value": 0.72, "confidence": 0.8}
```

bucket：

```text
very_low | low | medium | high | very_high
```

特征名：

```text
reasoning, context, unfamiliarity, tools, browser,
cross_file, test_quality, detectability, rollback,
ambiguity, horizon
```

`test_quality`、`detectability`、`rollback` 越高表示越安全；其他压力维度通常越高表示更难。

### Usage Estimate

```json
{
  "input_tokens": 100000,
  "uncached_input_tokens": 50000,
  "cached_input_tokens": 50000,
  "cache_write_tokens": 0,
  "output_tokens": 10000,
  "tool_cost": 0
}
```

约束：

- `input_tokens` 与 `uncached_input_tokens` 至少提供一个；
- 若提供总 input，`cached + cache_write` 不得超过总 input；
- `output_tokens` 必须表示计费输出，包括供应商计费口径中的 reasoning output；
- 不得重复计算 reasoning tokens；
- 所有值非负。

## Candidate

最小候选：

```json
{"model": "gpt-5.6-luna", "effort": "medium"}
```

注册表会补全 runtime、context、capability 和 pricing。

可覆盖字段：

```json
{
  "model": "gpt-5.6-luna",
  "effort": "medium",
  "product_mode": "standard",
  "snapshot": "gpt-5.6-luna",
  "runtimes": ["api"],
  "api_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
  "context_window": 1050000,
  "max_output_tokens": 128000,
  "capabilities": ["functions"],
  "effort_capacity": 0.5,
  "pricing": {},
  "estimated_task_cost": 0.5,
  "estimated_task_minutes": 2.0,
  "cost": 0.7,
  "minutes": 3.0
}
```

`cost` 和 `minutes` 是 benchmark proxy，置信度最低。

`product_mode` 与 API `reasoning.effort` 分离。宿主专用模式不得直接写入 API effort。

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

### 字段

| 字段 | 类型 | strict | 说明 |
|---|---|---:|---|
| `schema_version` | string | 是 | `1.0` |
| `source_id` | string | 是 | 必须在来源注册表 |
| `observed_at` | ISO-8601 | 是 | 缺失时非 strict 降权 |
| `subject.model` | string | 是 | 候选 model ID |
| `subject.effort` | string | exact 时是 | 被测 effort |
| `subject.snapshot` | string | 建议 | 模型快照 |
| `metric.name` | string | 是 | 必须被来源 allowlist 接受 |
| `metric.value` | finite number | 是 | 原始来源值 |
| `metric.higher_is_better` | boolean | 是 | 指标方向 |
| `metric.version` | string | 建议 | 指标/榜单版本 |
| `match` | enum | 建议 | `exact / family / proxy`；缺失降为 proxy |
| `sample_size` | number | 是 | strict 必须；非负 |
| `ci_half_width` | number | 建议 | 缺失降权；非负 |
| `harness` | string | 建议 | 可比实验环境 |
| `snapshot_id` | string | 是 | 来源快照 ID |
| `source_url` | HTTPS URL | 是 | provenance |
| `raw_sha256` | SHA-256 | 是 | 原始响应/快照哈希 |

### Panel ID

实现按以下字段分 panel：

```text
source_id
metric.name
metric.version
harness
snapshot_id
metric.higher_is_better
```

不要把不同 metric version 或 harness 强行归为一个 panel。

### Legacy candidate evidence

仍可读取旧式：

```json
{
  "model": "gpt-5.6-luna",
  "effort": "medium",
  "evidence": {
    "codexradar": {
      "score": 79,
      "age_hours": 1,
      "match": "exact"
    }
  }
}
```

但它会被标记为 legacy，缺失 provenance 会降权，strict 模式拒绝 legacy exact。开源集成应使用完整 envelope。

## Route Result

顶层主要字段：

```json
{
  "schema_version": "4.0",
  "route_id": "route_...",
  "algorithm_version": "select-model-v4",
  "generated_at": "...",
  "task": {},
  "task_input": {},
  "task_summary": "...",
  "selection_mode": "...",
  "selected": {},
  "fallback": {},
  "pareto_frontier": [],
  "ranking": [],
  "rejected": [],
  "evidence_report": {},
  "guards": {},
  "route_stability": {},
  "needs_more_context": false,
  "most_informative_missing_feature": null,
  "handoff": {},
  "audit": {},
  "warnings": []
}
```

### Selected candidate

在补全候选字段之外包含：

| 字段 | 含义 |
|---|---|
| `public_profile` | 来源信号、exact share、regret、置信度 |
| `raw_effort_penalty` | 未考虑 exact 证据前的通用 effort penalty |
| `effort_penalty` | exact share 衰减后的 penalty |
| `evidence_penalty` | 证据不确定性/分歧 penalty |
| `route_index` | 路由启发式 |
| `personal` | provisional 或 calibrated 个人结果 |
| `task_cost` | 成本、组成、来源、置信度、价格年龄 |
| `task_latency` | 延迟估计及来源 |

### Handoff

```json
{
  "model": "gpt-5.6-luna",
  "reasoning_effort": "medium",
  "product_mode": "standard",
  "plan": true,
  "target_runtime": "api",
  "risk": "medium",
  "required_capabilities": ["functions"]
}
```

## Attempt / History

完整输入：

```json
{
  "schema_version": 2,
  "attempt_id": "attempt-unique-id",
  "recorded_at": "2026-08-07T00:00:00Z",
  "route_id": "route_...",
  "task": {
    "task_type": "coding.refactor.cross_file",
    "risk": "medium",
    "repo_id": "repo-a",
    "environment_id": "linux-ci",
    "features": {}
  },
  "execution": {
    "model": "gpt-5.6-luna",
    "model_snapshot": "gpt-5.6-luna",
    "effort": "medium",
    "product_mode": "standard",
    "first_pass_success": true,
    "final_success": true,
    "tests_passed": true,
    "user_retry": false,
    "fallback_triggered": false,
    "quality_score": 0.95,
    "human_edit_minutes": 1.5,
    "latency_seconds": 35,
    "cost": 0.12,
    "usage": {
      "input_tokens": 100000,
      "cached_input_tokens": 50000,
      "cache_write_tokens": 0,
      "output_tokens": 10000,
      "tool_cost": 0
    }
  },
  "meta": {}
}
```

兼容旧字段 `execution.mode`，写入时规范化为 `product_mode`。

### Success 语义

优先顺序：

1. `first_pass_success`；
2. `success`；
3. `final_success && !retry && !fallback`；
4. `tests_passed && !retry && !fallback`。

个人校准默认预测首轮成功。

### 自动构造

`record --route --response --outcome` 会组合：

- route 中的 selected 和 task_input；
- response 中的 usage、response ID 和 latency；
- outcome 中的测试、成功、fallback、人工编辑和工具成本。

## Context Manifest

```json
{
  "required_capabilities": ["conversation", "files", "functions"],
  "portable_capabilities": ["conversation"],
  "previous_response_id": "resp_...",
  "reasoning_context": "all_turns",
  "instructions": "Preserve project constraints.",
  "input_history": [
    {"role": "user", "content": "Earlier request"},
    {"role": "assistant", "content": "Earlier result"}
  ],
  "input": "Continue the task",
  "file_ids": ["file_..."],
  "attach_file_ids": true,
  "tools": [
    {"type": "function", "name": "run_tests", "description": "...", "parameters": {}}
  ],
  "max_output_tokens": 10000,
  "metadata": {},
  "store": true
}
```

### 重要语义

- `portable_capabilities` 只用于审计对比，不授予能力；
- `file_ids` 默认不进入请求；必须 `attach_file_ids=true`；
- `input_history` 会真实复制到 outgoing input；
- `previous_response_id` 可形成 conversation capability；
- prompt override 会替换 context input，未发送的历史不会被计入 portable；
- host-only 状态永远不会因为声明而 portable。

## Collector Spec

```json
{
  "source_id": "codexradar",
  "location": "./snapshot.json",
  "records_path": "rows",
  "fields": {
    "model": "model",
    "effort": "effort",
    "value": "score",
    "observed_at": "observed_at",
    "sample_size": "sample_size",
    "ci_half_width": "ci_half_width",
    "snapshot": "model_snapshot",
    "harness": "harness"
  },
  "metric": {
    "name": "score",
    "higher_is_better": true,
    "version": "2026-08"
  },
  "model_map": {
    "provider-display-name": "canonical-model-id"
  },
  "match": "exact",
  "harness": "published-harness",
  "snapshot_id": "optional-fixed-snapshot-id",
  "source_url": "https://example.com/source",
  "strict": true
}
```

`location` 可为本地路径或公网 HTTPS JSON。远程响应最终 URL 和 SHA-256 会进入 collector output。

## 模型注册表

```json
{
  "schema_version": 1,
  "updated_at": "...",
  "aliases": {"gpt-5.6": "gpt-5.6-sol"},
  "models": {
    "model-id": {
      "display_name": "...",
      "snapshot": "...",
      "runtimes": ["api"],
      "api_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
      "effort_capacity": {"medium": 0.5},
      "context_window": 1000000,
      "max_output_tokens": 100000,
      "capabilities": ["functions"],
      "pricing": {
        "currency": "USD",
        "processing_mode": "standard",
        "input_per_million": 1,
        "cached_input_per_million": 0.1,
        "cache_write_multiplier": 1.25,
        "output_per_million": 6,
        "long_context_threshold": 272000,
        "long_context_input_multiplier": 2,
        "long_context_output_multiplier": 1.5,
        "observed_at": "...",
        "source_url": "https://..."
      }
    }
  }
}
```

注册表验证会检查 alias、effort capacity、价格非负、cache-write、长上下文倍率、时间和官方 HTTPS URL。

## 来源注册表

```json
{
  "schema_version": 1,
  "updated_at": "...",
  "unknown_sources": "reject",
  "sources": {
    "source-id": {
      "display_name": "...",
      "trust": 0.9,
      "ttl_hours": 72,
      "freshness_half_life_hours": 36,
      "sample_scale": 500,
      "accepted_metrics": ["score"],
      "official_urls": ["https://..."],
      "task_relevance": {
        "coding": 1,
        "agent": 0.7,
        "research": 0.2,
        "writing": 0.1,
        "analysis": 0.3,
        "general": 0.35
      }
    }
  }
}
```

## 兼容性

- Route schema：支持旧 `1.0` 基础输入，但新输出为 `4.0`；
- Evidence：允许旧 candidate-local evidence，但 strict exact 拒绝；
- History：接受旧 `mode`，规范化为 `product_mode`；
- API effort：必须由当前模型注册表决定；
- 新字段应保持向后兼容，删除或改变语义需要主版本升级；
- 输出消费者不应依赖对象字段顺序；
- 任何算法或注册表更新都应保存版本、日期和指纹。
