# Select Model 自动化指南

本文给出从“偶尔手工推荐”升级到“持续采集、自动路由、真实反馈、可审计发布”的参考流程。

## 目录

- [自动化分层](#自动化分层)
- [推荐目录与状态](#推荐目录与状态)
- [证据采集流水线](#证据采集流水线)
- [路由流水线](#路由流水线)
- [执行与反馈流水线](#执行与反馈流水线)
- [每日工作总结系统集成](#每日工作总结系统集成)
- [CI 与定时任务](#ci-与定时任务)
- [监控指标](#监控指标)
- [故障与降级](#故障与降级)
- [隐私和安全](#隐私和安全)
- [生产上线清单](#生产上线清单)

## 自动化分层

建议分四级实施：

| 级别 | 能力 | 适用阶段 |
|---|---|---|
| L0 Advisor | 手工 task + evidence，输出推荐 | 验证规则 |
| L1 Managed Evidence | 定时 collector、证据仓库、自动 build | 稳定使用 |
| L2 Feedback Calibrated | 自动记录 usage/outcome，个人校准 | 高频重复任务 |
| L3 Host-native Execution | 宿主保存上下文并自动执行/fallback | 生产集成 |

不要直接从 L0 跳到无审计的全自动执行。先保证证据、成本和反馈数据可靠。

## 推荐目录与状态

用户状态不应写入 Git 仓库：

```text
~/.select-model/
├── evidence.jsonl
├── history.jsonl
├── snapshots/
├── routes/
└── logs/
```

仓库保存：

```text
config/models.json
config/sources.json
collector specs
schema/tests/docs
```

建议环境变量：

```bash
export SELECT_MODEL_HOME="$HOME/.select-model"
export SELECT_MODEL_HASH_SALT='local-secret'
export OPENAI_API_KEY='...'
```

`SELECT_MODEL_HASH_SALT` 不应提交到 Git，也不应在不同组织间复用。

## 证据采集流水线

### 1. 为稳定 JSON 数据创建 mapping spec

```json
{
  "source_id": "codexradar",
  "location": "https://public.example/benchmark.json",
  "records_path": "rows",
  "fields": {
    "model": "model_id",
    "effort": "effort",
    "value": "score",
    "sample_size": "n",
    "ci_half_width": "ci",
    "observed_at": "updated_at"
  },
  "metric": {
    "name": "score",
    "higher_is_better": true,
    "version": "provider-v1"
  },
  "match": "exact",
  "harness": "provider-harness",
  "strict": true
}
```

### 2. 采集并直接导入仓库

```bash
python scripts/select_model.py evidence collect \
  --spec collector-spec.json \
  --output "$SELECT_MODEL_HOME/snapshots/latest.json" \
  --store "$SELECT_MODEL_HOME/evidence.jsonl"
```

collector 会：

1. 验证来源已注册；
2. 对远程 URL 做 HTTPS、凭据、DNS、公网 IP 和 redirect 检查；
3. 限制响应大小；
4. 解析 JSON；
5. 映射字段；
6. 计算 raw SHA-256；
7. 严格验证 envelope；
8. 输出 rejected/skipped 行；
9. 用 `evidence_id` 去重导入。

### 3. 处理没有稳定 API 的来源

不要在核心仓库内写易碎 HTML selector。推荐：

1. 在独立 adapter/CI job 中获取并规范化数据；
2. 保存原始快照；
3. 生成稳定 JSON；
4. 用通用 collector 或 `evidence import` 导入；
5. 保存来源 URL、抓取时间、adapter 版本和 SHA-256。

登录态、反机器人或需要浏览器的来源应由宿主工具处理，不应把 cookie/token 写入 collector spec。

### 4. 导出未过期最新证据

```bash
python scripts/select_model.py evidence latest \
  --store "$SELECT_MODEL_HOME/evidence.jsonl" \
  --output "$SELECT_MODEL_HOME/latest-evidence.json"
```

latest 的去重维度包括来源、模型、effort、metric 和 harness；超过来源 TTL 的记录不会导出。

## 路由流水线

### 1. 自动生成 task

宿主可从请求和运行环境推断：

- task type；
- required capabilities；
- context/output token estimate；
- repo/environment ID；
- 是否有测试和回滚；
- 工具/browser/cross-file/horizon；
- usage estimate 和 failure cost。

对无法可靠推断的维度应使用低 confidence，而不是猜精确值。

### 2. 构造 route input

```bash
python scripts/select_model.py build \
  --task task.json \
  --candidates candidates.json \
  --evidence-store "$SELECT_MODEL_HOME/evidence.jsonl" \
  --output "$SELECT_MODEL_HOME/route-input.json"
```

### 3. 路由

```bash
python scripts/select_model.py route \
  --input "$SELECT_MODEL_HOME/route-input.json" \
  --history "$SELECT_MODEL_HOME/history.jsonl" \
  --output "$SELECT_MODEL_HOME/routes/current.json"
```

高风险失败时不要自动放宽：

- 无 candidate：停止并更新 registry/任务要求；
- 证据不足：补采证据或人工指定质量优先候选；
- task confidence 低：询问 `most_informative_missing_feature`；
- cost unknown：使用质量优先，不虚构成本节约。

## 执行与反馈流水线

### Host-native 推荐流程

```text
route
 -> host checks approval/budget
 -> execute_selected_model(model, effort, execution_context_ref)
 -> collect response usage and latency
 -> run tests / evaluate output
 -> write outcome
 -> record attempt
 -> fallback if policy allows
```

宿主应记录：

- route ID；
- selected 与 fallback；
- execution context reference；
- model snapshot；
- input/cached/cache-write/output tokens；
- tool/search cost；
- latency；
- first-pass success；
- tests；
- retry/fallback；
- final success；
- human edit minutes；
- quality score。

### Responses API fallback

先 dry-run：

```bash
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --dry-run
```

通过 context report 后再 live：

```bash
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --output api-response.json
```

自定义 endpoint 必须使用独立 key：

```bash
export INTERNAL_ROUTER_KEY='...'
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --endpoint https://gateway.example/v1/responses \
  --allow-custom-endpoint \
  --api-key-env INTERNAL_ROUTER_KEY
```

`OPENAI_API_KEY` 不会被发送到自定义 endpoint。

### 自动记录

准备 outcome：

```json
{
  "first_pass_success": true,
  "final_success": true,
  "tests_passed": true,
  "user_retry": false,
  "fallback_triggered": false,
  "quality_score": 0.95,
  "human_edit_minutes": 1.5,
  "latency_seconds": 42,
  "tool_cost": 0
}
```

记录：

```bash
python scripts/select_model.py record \
  --route route-result.json \
  --response api-response.json \
  --outcome outcome.json \
  --history "$SELECT_MODEL_HOME/history.jsonl" \
  --hash-identifiers
```

重复 `attempt_id` 不会重复写入。

## 每日工作总结系统集成

用户已有每日工作记录网站时，可将路由结果与任务结果作为结构化附件写入每条工作记录。

### 最小字段

```json
{
  "work_item_id": "daily-2026-08-07-001",
  "task_type": "coding.refactor.cross_file",
  "repo_id": "repo-a",
  "environment_id": "linux-ci",
  "route_id": "route_...",
  "model": "gpt-5.6-luna",
  "effort": "medium",
  "first_pass_success": true,
  "fallback_triggered": false,
  "human_edit_minutes": 1.5,
  "usage": {},
  "latency_seconds": 42
}
```

### 推荐触发点

1. 创建工作项：生成 task signature；
2. 启动任务：构造 route input 并保存 route ID；
3. 完成模型调用：保存 usage/latency；
4. 测试或人工验收：生成 outcome；
5. 关闭工作项：调用 `record`；
6. 每日汇总：展示模型、成本、首轮成功、fallback 和人工编辑；
7. 每周复盘：运行 `stats` 并检查任务族数据是否足以校准。

### 隐私最小化

推荐保存：

- hash 后的 repo/environment；
- 任务类型和粗粒度特征；
- 用量、成本、延迟和结果；
- route/attempt ID。

不必保存：

- 完整 prompt；
- 私有代码正文；
- API key；
- 浏览器 cookie；
- connector token；
- 客户原始数据。

## CI 与定时任务

### 仓库 CI

本仓库 GitHub Actions 应执行：

```bash
python -m pip install -e .
python scripts/release_check.py
```

测试矩阵覆盖 Python 3.11、3.12、3.13。

### 每日证据任务

伪 cron：

```cron
15 */6 * * * cd /opt/select-model && ./ops/collect-all.sh
30 */6 * * * cd /opt/select-model && python scripts/select_model.py doctor --evidence-store ~/.select-model/evidence.jsonl
```

不同来源可使用不同频率，频率不应低于注册表 TTL 所需的最低更新节奏。

### 每周事实审计

```bash
python scripts/select_model.py doctor --strict
python scripts/select_model.py registry validate --kind models --input config/models.json
python scripts/select_model.py registry validate --kind sources --input config/sources.json
```

如果价格或能力已变，更新注册表、CHANGELOG、示例快照和测试预期。

## 监控指标

### 路由质量

- selected/fallback 分布；
- route stability 分布；
- rejected candidate 原因；
- evidence source count；
- exact share；
- task-vector confidence；
- 需要补充信息的比例。

### 结果质量

- 首轮成功率；
- 最终成功率；
- fallback 率；
- 用户重试率；
- 测试通过率；
- human edit minutes；
- quality score。

### 成本和速度

- 每个成功任务的总成本；
- token 成本与工具成本；
- 缓存命中比例；
- cache-write 成本；
- latency；
- 不同 task family/model/effort 的成本和首轮成功。

### 数据健康

- 证据新鲜度；
- collector skipped/rejected；
- JSONL invalid lines；
- registry age；
- 个人校准 effective_n；
- model snapshot 漂移。

## 故障与降级

### Collector 失败

- 保留上一个未过期快照；
- 超 TTL 后自动剔除，不延长寿命；
- 记录失败，不把空采集当成功；
- 高风险来源不足时停止路由或质量优先人工处理。

### Registry 更新失败

- 使用 SHA pin；
- 先下载到临时文件并验证；
- 验证通过后原子替换；
- 失败时保留旧版本；
- 不允许半写入文件。

### History 损坏

- `doctor --history` 报告 invalid lines；
- 备份原文件；
- 逐行恢复合法 JSON；
- 不应用猜测补齐成功结果。

### Dispatch 失败

- 区分资格/上下文拒绝、认证、HTTP、网络和模型错误；
- API 失败不自动标记模型任务失败，除非结果定义如此；
- fallback 前检查幂等性、重复副作用和预算；
- 高风险写操作必须有审批和 rollback。

## 隐私和安全

- API key 只从环境或 secret store 读取；
- 自定义 endpoint 使用独立 key；
- collector 不接受带凭据 URL；
- 远程 source 必须公网 HTTPS；
- dry-run 默认脱敏；
- route/history/evidence 文件可能包含项目标识，按敏感日志处理；
- 生产中为 history/evidence 配置文件权限和备份；
- 用户可通过 hash identifiers 降低标识泄露风险；
- 不把模型输出自动视为安全或正确。

## 生产上线清单

- [ ] 模型和来源注册表已由两人或自动签名流程审核；
- [ ] 价格、context、capability、effort 与 runtime 有官方来源和日期；
- [ ] 每个证据来源有 TTL、metric allowlist 和任务相关性；
- [ ] collector 失败可观测；
- [ ] route input 固定 `as_of` 便于重放；
- [ ] high/critical 使用 strict；
- [ ] 成本包含 cache-write、long-context 和 tool cost；
- [ ] history 不保存完整 prompt/私有代码；
- [ ] `record` 在任务结束时自动调用；
- [ ] dispatch 前 dry-run/审批；
- [ ] 自定义 endpoint 不使用 OpenAI key；
- [ ] fallback 有幂等性和预算保护；
- [ ] CI 运行 release check；
- [ ] 关键不变量有回归测试；
- [ ] 定期查看每个成功任务总成本，而不是只看单次 token 价格。
