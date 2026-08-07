# Select Model 架构说明

本文描述 Select Model 4.x 的设计边界、数据流、核心算法、安全不变量和扩展方式。主 README 面向使用者；本文面向维护者、审计者和集成人员。

## 目录

- [目标与非目标](#目标与非目标)
- [系统边界](#系统边界)
- [组件](#组件)
- [端到端数据流](#端到端数据流)
- [任务画像](#任务画像)
- [候选资格检查](#候选资格检查)
- [证据系统](#证据系统)
- [成本与延迟](#成本与延迟)
- [个人校准](#个人校准)
- [候选选择](#候选选择)
- [稳定性与审计](#稳定性与审计)
- [安全执行](#安全执行)
- [自动化与持久化](#自动化与持久化)
- [故障模型](#故障模型)
- [扩展点](#扩展点)
- [不可破坏的不变量](#不可破坏的不变量)

## 目标与非目标

### 目标

Select Model 试图回答：

> 在给定任务、风险、运行时、公开证据、真实成本和个人历史的情况下，哪一个 `model + effort + product mode` 足够可靠，同时拥有更低的成功任务总成本？

具体目标：

1. 对模型和推理强度做可审计、可重复的选择；
2. 对高风险任务默认 fail-closed；
3. 不把异构 benchmark 原始分数伪装成统一能力分；
4. 不把公共排行榜伪装成用户任务成功率；
5. 优先使用任务级价格和真实用量；
6. 用真实个人结果逐步校准，而不是用主观印象；
7. 将易变事实移出算法，放入版本化注册表；
8. 在执行前证明所需上下文真实进入请求；
9. 所有关键安全行为都可通过回归测试固定。

### 非目标

Select Model 不承诺：

- 证明某个模型在所有任务上最优；
- 将不同榜单转换成可比较的“智力分”；
- 在没有真实个人记录时输出可信的任务成功概率；
- 静默控制 ChatGPT 当前 turn 的模型选择器；
- 自动继承 IDE、repo、浏览器登录、connector 或隐藏系统指令；
- 替代测试、权限控制、人工审批、回滚和结果审查；
- 充当通用网页爬虫或登录态浏览器。

## 系统边界

```text
┌──────────────────────────────────────────────────────────────┐
│ 外部事实                                                     │
│ 模型/价格文档  Benchmark/API/快照  用户真实执行结果          │
└──────────────┬──────────────────────┬────────────────────────┘
               │                      │
               v                      v
┌──────────────────────┐    ┌──────────────────────┐
│ models/sources registry│    │ evidence/history JSONL│
└───────────┬──────────┘    └───────────┬──────────┘
            │                           │
            └────────────┬──────────────┘
                         v
                ┌─────────────────┐
                │ deterministic router│
                └───────┬─────────┘
                        │
         ┌──────────────┼──────────────────┐
         v              v                  v
   推荐与 fallback   host-native handoff   新 Responses API 请求
                                         （仅可移植上下文）
```

核心 router 不联网。联网行为仅存在于显式 collector、registry sync 和 dispatch 命令中。

## 组件

| 组件 | 文件 | 职责 |
|---|---|---|
| CLI | `select_model/cli.py` | 参数解析、文件输入输出、命令编排 |
| Router | `select_model/router.py` | 资格、证据、成本、保护阈值、Pareto 与选择 |
| Task profile | `select_model/task_profile.py` | 任务特征、压力、风险和置信度 |
| Evidence | `select_model/evidence.py` | envelope 校验、panel 排名、稳健聚合、regret |
| Cost | `select_model/cost.py` | 输入/缓存/缓存写入/输出/工具/长上下文成本 |
| History | `select_model/history.py` | 结果记录、去重、时间衰减、个人校准 |
| Registry | `select_model/registry.py` | 模型与来源事实校验、候选补全、指纹 |
| Dispatch | `select_model/dispatch.py` | 请求构造、可移植性证明、API 调用 |
| Automation | `select_model/automation.py` | JSON collector、证据仓库、route builder、registry sync |
| Doctor | `select_model/doctor.py` | 安装、事实时效和数据完整性诊断 |
| Schema facade | `select_model/schemas.py` | 外部集成所需的轻量校验入口 |

项目运行时仅依赖 Python 标准库。

## 端到端数据流

### 路由阶段

```text
route input
  1. schema/必填字段校验
  2. task profile + eligibility mode
  3. registry hydrate candidate
  4. runtime/capability/context/output/effort precheck
  5. evidence envelope 校验与 TTL 过滤
  6. 在 panel 内对候选排名
  7. 按来源得到候选 signal，再稳健聚合
  8. effort/reliability/evidence penalty
  9. task cost + latency
 10. capability floor + source regret guard
 11. Pareto frontier
 12. 公共证据成本保护选择
 13. 可用时加入个人校准
 14. stability + fallback + audit fingerprint
```

### 反馈阶段

```text
route result + API response + observed outcome
  -> build_attempt_from_artifacts
  -> 隐私处理/哈希标识
  -> attempt_id 去重
  -> JSONL append + fsync + file lock
  -> 后续 routing 的个人校准和 usage 估计
```

## 任务画像

任务使用 11 个粗粒度维度：

```text
reasoning, context, unfamiliarity, tools, browser,
cross_file, test_quality, detectability, rollback,
ambiguity, horizon
```

每个特征可写成：

```json
"reasoning": "high"
```

或：

```json
"reasoning": {"bucket": "high", "confidence": 0.8}
```

也支持数值，但仅应在确有量化依据时使用：

```json
"reasoning": {"value": 0.72, "confidence": 0.85}
```

缺失特征使用低置信度默认值。总体 task-vector confidence 是各维度置信度的加权结果。路由器不会因为缺失信息制造精确小数，而会缩窄允许的降级窗口。

### 压力分解

任务画像聚合为：

- `reasoning`：推理、跨文件、歧义、长期链路和陌生度；
- `context`：上下文、跨文件、陌生度和 horizon；
- `tools`：工具、浏览器、horizon 和跨文件；
- `reliability`：测试不足、错误难检测、难回滚和风险；
- `overall`：加权平均与最大单项压力的组合。

`overall` 仅用于路由策略，不是任务客观难度证明。

### 资格模式

- `strict`：关键事实未知即拒绝；
- `balanced`：同样拒绝关键运行时/capability/context 未知，但证据要求略低；
- `explore`：允许未知项进入，但添加显著惩罚和 warning；
- `auto`：根据风险映射，高/critical 默认 strict。

## 候选资格检查

资格检查发生在任何成本比较之前。检查项包括：

1. 模型是否存在于注册表；
2. 目标 runtime 是否支持；
3. API effort 是否在 allowlist；
4. required capabilities 是否可验证并满足；
5. estimated context 是否超过 context window；
6. estimated output 是否超过 max output；
7. effort capacity 是否存在；
8. product mode 是否与执行路径兼容。

### Fail-closed 规则

在 `strict` 和 `balanced` 中：

- 模型未注册：拒绝；
- runtime 未知：拒绝；
- required capability 未知：拒绝；
- context window 未知：拒绝；
- API effort 未知：拒绝。

`explore` 只适用于低风险、强验证、可回滚实验。它不会让未知事实变成已知，只会保留候选并记录 penalty。

## 证据系统

### Evidence Envelope

每条证据必须说明：

- 来源；
- 观察时间；
- 被测 model、effort、snapshot；
- 指标名、值、方向和版本；
- exact/family/proxy 匹配；
- 样本量和不确定性；
- harness 与来源 snapshot；
- 来源 URL 和原始内容 SHA-256。

完整字段见 [Schema 文档](schema.zh-CN.md)。

### 为什么不平均原始分数

Arena percentile、某 benchmark pass rate、综合 intelligence index 和 resolved rate 测量不同构念。即使都写成 0–100，也不代表数值差可比较。

4.x 使用 panel：

```text
source_id
+ metric.name
+ metric.version
+ harness
+ snapshot_id
+ higher_is_better
```

同一 panel 内候选按原始值排名。并列候选得到相同中间排名。随后将排名转换为 `0–100` rank percentile。只有这些来源内部相对信号才进入跨来源聚合。

### 权重

证据权重大致由以下因素相乘：

```text
source trust
× task-family relevance
× exact/family/proxy match
× freshness
× sample sufficiency
× uncertainty
× provenance completeness
```

安全默认：

- 缺 `match`：降为 proxy；
- 缺时间：严重降权，strict 拒绝；
- 缺样本：降权，strict 拒绝；
- 缺 uncertainty：降权；
- 缺 provenance：降权，strict 拒绝；
- 未注册来源：拒绝；
- 超 TTL：拒绝；
- 未来时间戳：拒绝；
- exact model/effort 不匹配：拒绝。

“少填信息”绝不能提高权重，这是测试固定的不变量。

### 聚合

1. panel 内排名；
2. 同一来源的多个 panel 信号按权重汇总；
3. 不同来源用加权中位数形成 `routing_index`；
4. 保留来源内部 regret；
5. 计算来源分歧 MAD、exact share、source count、有效权重和 confidence。

单一来源的 evidence confidence 和 route stability 均有硬上限。

## 成本与延迟

成本组件：

```text
uncached input
+ cached input
+ cache write
+ output/reasoning output
+ tool/search cost
```

如果 prompt 超过注册表长上下文阈值，可分别应用 input 和 output multiplier。

### 成本来源优先级

1. `pricing + task.usage_estimate`；
2. `pricing + similar personal usage`；
3. `candidate.estimated_task_cost`；
4. `candidate.cost` benchmark proxy；
5. unknown。

缓存写入价格必须有显式 rate 或 multiplier。缺失时返回 cost unknown，不能用普通输入价冒充保守估计。

价格超过 30 天会降低置信度；超过 90 天标记为低置信度。`doctor` 用相同的 90 天政策提示事实漂移。

## 个人校准

个人校准只使用真实结果。默认目标是首轮成功，而不是“经过多次 fallback 最终成功”。

### 相似性

权重考虑：

- task type 是否完全相同；
- task family 是否相同；
- 11 维任务向量距离；
- repo 是否相同；
- environment 是否相同；
- 记录时间衰减；
- model snapshot 是否一致；
- model、effort、product mode 是否一致。

同 model 不同 effort 只能贡献受限先验，不能替代同配置样本。

### 时间衰减

默认 half-life 为 120 天。旧记录仍可提供弱信息，但不会永久等权影响当前路由。缺少 recorded_at 的遗留记录只给予折扣权重。

### 输出门槛

只有同时满足以下条件才输出 `p_success`：

- 同配置有效样本 `effective_n >= 8`；
- 同任务族真实记录不少于 10；
- 完全相同 task type 记录不少于 4。

门槛前只返回 `provisional_p_success` 和区间，并明确 `available=false`。路由器不会把 provisional 值称为已校准成功率。

## 候选选择

### 路由指数

候选路由指数由：

```text
public routing index
- effort under-budget penalty
- evidence uncertainty penalty
- explore-mode unknown eligibility penalty
```

构成。exact model+effort 证据已经直接观测该 effort，因此通用 effort penalty 会按 exact share 衰减，避免重复惩罚。

### 保护阈值

- `capability floor`：候选不能落后最佳路由指数过多；
- `source regret limit`：不能在可靠 exact 来源上落后过多；
- `effort penalty ceiling`：明显低配的 effort 不能仅凭便宜入选；
- `minimum evidence sources`：高风险默认至少两个独立来源；
- `require exact evidence`：strict 默认至少有 exact model+effort 证据。

### Pareto frontier

在成本、延迟、路由指数三个轴上被其他候选同时支配的候选被移除。

### 公共证据成本保护

最高质量候选是参考。较便宜候选只有在：

1. 保持在 capability/regret/effort guard 内；
2. 质量损失与任务风险允许；
3. 成本节约达到与质量损失、风险和成本置信度匹配的阈值；

时才可被选择。

### 个人校准路径

已校准候选可参与 expected total cost：

```text
(direct model + time cost) / p_success
+ (1 - p_success) × failure cost
```

未校准候选仍保留公共证据路径。个人数据不会因为 frontier 中另一个候选未校准而整体失效。

## 稳定性与审计

### Stability

稳定性综合：

- task-vector confidence；
- selected evidence confidence；
- 前两名来源重叠；
- top gap；
- 逐来源移除后的 winner jackknife。

单一来源或低证据置信度时，稳定性上限被硬性压低。

### Audit

每次 route 输出：

- `route_id`；
- `algorithm_version`；
- `generated_at`；
- 输入、模型注册表和来源注册表指纹；
- accepted/rejected evidence；
- rejected candidates；
- guard 值；
- selection mode；
- fallback。

相同输入和相同注册表在相同 `as_of` 下应产生确定性结果。

## 安全执行

### Host-native 优先

最安全的自动执行接口是宿主提供：

```text
execute_selected_model(model, effort, execution_context_ref)
```

宿主负责保留 repo、分支、文件、工具、认证、审批和对话状态。

### Responses API fallback

`dispatch` 只能创建新请求。它从最终 outgoing payload 推导 portable capabilities，而不相信声明字段。

可观测映射：

- conversation：`previous_response_id` 或真实多轮消息；
- files：真实 `input_file` part；
- mcp/web/computer/file_search/functions：真实 tools；
- instructions：真实 instructions 字段。

以下是 host-only 状态：

```text
repo, local_repo, workspace, tool_state,
browser_session, ide_state, shell_state
```

声明这些状态不会使其可移植。

### Context loss

普通风险缺上下文：默认拒绝；需要 `allow_context_loss` 才能覆盖。

高/critical 风险：除上述开关外，还必须显式设置 `force_high_risk_context_loss`。两个开关防止一个宽泛参数意外绕过高风险保护。

### 自定义 endpoint

- 默认只允许官方 Responses endpoint；
- 自定义 endpoint 必须显式允许；
- URL 必须为 HTTPS、无内嵌凭据、无 fragment；
- 实际发送时必须使用独立 API key 环境变量；
- 路由器拒绝把 `OPENAI_API_KEY` 发送到自定义 endpoint。

## 自动化与持久化

### Evidence store

证据以 JSONL 保存：

- `evidence_id` 去重；
- append + flush + fsync；
- Unix 上使用文件锁；
- 损坏行被报告但不阻止读取其他记录；
- latest 导出时按来源 TTL 过滤。

### History store

历史同样使用 JSONL：

- `attempt_id` 去重；
- 可哈希 repo/environment；
- 不要求保存完整 prompt 或私有代码；
- 记录首轮成功、最终成功、重试、fallback、人工编辑、质量、成本和用量。

### Collector

collector 只接 JSON API 或本地 JSON 快照。远程读取：

- 必须 HTTPS；
- 拒绝 URL 凭据；
- 拒绝解析到 loopback/private/link-local 等非公网地址；
- 每次 redirect 重新验证；
- 下载大小默认上限 20 MiB；
- 保存最终 URL 和 raw SHA-256。

## 故障模型

| 故障 | 默认行为 |
|---|---|
| 模型未注册 | balanced/strict 拒绝 |
| 来源未注册 | 拒绝证据 |
| 证据过期 | 剔除 |
| 证据元数据缺失 | 降权或 strict 拒绝 |
| 没有任何可用候选 | 路由失败，不猜测 |
| 所有成本未知 | 质量优先，不伪造节约 |
| JSONL 某行损坏 | 跳过并产生 warning |
| 高风险上下文缺失 | dispatch 拒绝 |
| API key 缺失 | live dispatch 拒绝 |
| 自定义 endpoint 使用 OpenAI key | 拒绝 |
| collector 跳转到私网 | 拒绝 |
| 注册表 SHA 不匹配 | sync 拒绝且不覆盖目标文件 |

## 扩展点

### 新模型

只修改 `config/models.json`，补充：

- runtime；
- API efforts；
- effort capacity；
- context/output；
- capability；
- pricing、长上下文规则、核验时间和官方 URL。

算法不应为单一模型写特殊分支。

### 新证据来源

1. 在 `config/sources.json` 注册；
2. 定义 accepted metrics、TTL、half-life、sample scale 和任务相关性；
3. 提供 official URL；
4. 新建 collector mapping 或独立 adapter；
5. 增加来源量纲、方向、harness 和时效测试；
6. 证明缺失字段不会增信。

### 新 runtime

扩展 registry runtime 和 host adapter。不要把产品 surface mode 塞入 Responses API `reasoning.effort`。

### 新存储后端

核心函数接收 Python dict/list，可替换 JSONL 为 SQLite 或远程仓库。后端仍应保证：去重、原子性、并发安全、时间戳和审计指纹。

## 不可破坏的不变量

任何贡献都必须保持：

1. 缺少证据元数据不能提高权重；
2. 未注册来源不能进入正式路由；
3. 过期证据不能进入聚合；
4. 异构原始 benchmark 值不能直接算术平均；
5. high/critical 风险关键资格未知必须 fail-closed；
6. 单一弱来源不能产生 high stability；
7. cache-write 缺价时不能低价兜底；
8. 公共 routing index 不能标记为 task success probability；
9. provisional history 不能标记为 calibrated；
10. portable capability 必须能从最终请求体观察；
11. host-only 状态不能通过声明字段凭空产生；
12. `OPENAI_API_KEY` 不能发送到自定义 endpoint；
13. registry sync 在校验失败或 SHA 不符时不能覆盖目标；
14. 提高风险不能导致更弱的公共路由保护；
15. 所有行为变更必须附带测试。
