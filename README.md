# Select Model

一个可审计、保守、可自动化的模型与推理强度路由器。

Select Model 根据任务压力、风险、运行时限制、当前公开证据、真实任务成本和个人执行历史，选择“足够可靠且总成本更低”的模型配置。它既可以作为 ChatGPT Skill 使用，也可以作为无第三方运行时依赖的 Python CLI/库集成到 API、Codex、IDE、CI 或内部工作流中。

> 核心边界：公共 benchmark 只提供路由先验，不是模型的统一智力分，也不是 `P(完成你的任务)`。个人成功概率只来自足量、相似、真实、时间衰减后的执行结果。

当前版本：**4.0.0**  
最低 Python：**3.11**  
许可证：**MIT**

---

## 为什么需要它

“总是用最强模型”通常可靠但昂贵；“简单任务用便宜模型”又过于粗糙。真实选择至少同时受以下因素影响：

- 任务是否需要长链推理、跨文件理解、工具编排或浏览器操作；
- 错误是否容易发现、是否有测试、是否可以回滚；
- 候选在当前任务类型和当前运行时上的证据是否完整、及时且可比；
- 输入、缓存读取、缓存写入、输出、搜索与工具调用的真实成本；
- 用户在相同任务族、仓库和环境中的实际首次成功率；
- 模型、价格、上下文窗口和产品能力随时间变化造成的漂移。

Select Model 将这些因素拆成可审计的阶段：

```text
任务画像
  -> 候选资格检查
  -> 证据验证与来源内排名
  -> 推理强度/可靠性惩罚
  -> 任务级成本与延迟估计
  -> 风险和证据后悔值保护
  -> Pareto 剪枝
  -> 保守成本优化
  -> 可选个人校准
  -> handoff / 执行 / 反馈
```

---

## 4.0 解决了什么

| 旧问题 | 4.0 行为 |
|---|---|
| 缺少时间、样本量或区间反而得到高权重 | 缺失元数据会降权；严格模式直接拒绝关键缺失 |
| 未知来源仍可进入聚合 | 未列入 `config/sources.json` 的来源默认拒绝 |
| 过期证据长期以最低权重存活 | 超过来源 TTL 直接剔除 |
| 不同 benchmark 的 0–100 数值被当成同一量纲 | 先在同一来源/指标/harness/snapshot 内排名，再跨来源稳健聚合 |
| 高风险下 capability/context 未知仍放行 | 高风险自动 `strict`，资格未知即拒绝 |
| 单一弱来源可显示高稳定性 | 单来源稳定性硬封顶；增加来源移除 jackknife |
| 缓存写入缺价时按普通输入价计算 | 缺失即成本未知；显式支持 cache-write multiplier |
| 长上下文价格未进入任务成本 | 支持阈值、输入倍数和输出倍数 |
| 精确 model+effort 证据又被完整 effort penalty 重复惩罚 | 根据 exact evidence 覆盖率衰减通用 effort penalty |
| 所有 Pareto 候选都校准后才使用个人历史 | 已校准候选可局部参与，未校准候选仍保留公共证据路径 |
| 历史永久有效、跨版本污染 | 时间衰减、模型快照降权、任务族/仓库/环境隔离 |
| `file_ids` 或声明字段产生上下文迁移假阳性 | 只根据最终 API 请求体中的可观测内容判定 portable capability |
| 自动执行可能静默丢失高风险上下文 | 高风险 context loss 需要两个独立显式开关 |
| 模型与价格硬编码在算法中 | 移至版本化模型注册表和来源注册表 |
| 手工构造证据和反馈成本高 | 通用 JSON collector、证据仓库、route builder、自动 outcome record |

---

## 设计原则

### 1. 先判断“有无资格”，再优化价格

候选必须先通过运行时、effort、capability、上下文和输出长度检查。`strict` 和 `balanced` 不接受关键能力未知；只有显式 `explore` 允许带惩罚试验。

默认策略：

| 风险 | 默认资格模式 | 默认证据要求 |
|---|---|---|
| low | balanced | 至少 1 个来源 |
| medium | balanced | 至少 1 个来源 |
| high | strict | 至少 2 个独立来源，且包含 exact model+effort |
| critical | strict | 至少 2 个独立来源，且包含 exact model+effort |

### 2. 不混合异构 benchmark 原始量纲

每条证据属于一个 panel：

```text
source + metric + metric_version + harness + snapshot_id + direction
```

候选只在同一个 panel 中排名，得到来源内部的 rank percentile。不同来源的 rank signal 再按任务相关性、时效、样本、匹配类型、区间和 provenance 加权，并用加权中位数聚合。

### 3. 不把公共榜单伪装成个人成功率

`routing_index` 只是候选排序启发式。只有真实个人执行历史达到门槛时，才输出 `p_success`：

- 同一 model+effort 的有效相似样本不少于 8；
- 同任务族真实记录不少于 10；
- 完全相同 task type 的真实记录不少于 4；
- 历史按时间、模型快照、仓库和环境加权；
- 相邻 effort 只能作为受限的弱先验。

### 4. 优化的是成功任务总成本

优先级：

1. 当前价格 × 当前任务 usage estimate；
2. 当前价格 × 相似个人任务的真实 usage；
3. 显式任务成本估算；
4. benchmark 平均成本代理，仅标记为低置信度。

总决策还可加入人工时间价值和失败成本：

```text
模型成本 + 工具成本 + 人工时间 + 失败/重试/返工成本
```

### 5. 自动执行必须证明上下文真实进入请求

`dispatch` 是一个新的 Responses API 请求，不继承当前 ChatGPT/Codex/IDE 的隐藏状态。只有最终 payload 中真实存在的历史、文件或工具才算 portable。声明 `portable_capabilities` 不会让上下文凭空存在。

---

## 快速开始

### 直接从仓库运行

```bash
git clone YOUR_REPOSITORY_URL
cd select-model
python scripts/select_model.py --version
make check
```

### 安装为命令行工具

```bash
python -m pip install -e .
select-model --version
```

项目运行时只使用 Python 标准库。构建 editable package 需要 setuptools。

### 第一次路由

```bash
python scripts/select_model.py route \
  --input examples/route-input.json \
  --history /tmp/select-model-history.jsonl \
  --format markdown
```

示例输出：

```text
任务：coding / normal / risk=medium / eligibility=balanced
推荐：gpt-5.6-sol / medium
依据：当前证据、风险保护和任务级成本
稳定性：high / medium / low
```

机器可读 JSON：

```bash
python scripts/select_model.py route \
  --input examples/route-input.json \
  --output route-result.json
```

---

## 作为 ChatGPT Skill 使用

目录本身符合 Skill 结构：

```text
select-model/
├── SKILL.md
├── agents/openai.yaml
├── scripts/
├── references/
└── assets/
```

将项目打包成 `skill.zip` 后即可上传。Skill 负责指导 ChatGPT：

1. 获取与任务相关的当前证据；
2. 填充任务、候选与证据 envelope；
3. 调用 deterministic router；
4. 返回推荐、依据、fallback、个人校准和稳定性；
5. 只有在上下文可移植时才执行新 API 请求。

Skill 不能自行改变当前 ChatGPT turn 已选择的模型。无缝切换应由宿主提供：

```text
execute_selected_model(model, effort, execution_context_ref)
```

---

## CLI 总览

```bash
python scripts/select_model.py --help
```

| 命令 | 作用 |
|---|---|
| `route` | 执行完整路由 |
| `profile` | 仅生成任务压力画像 |
| `record` | 记录真实执行结果，或从 route/response/outcome 自动生成 |
| `stats` | 查看个人历史统计 |
| `doctor` | 检查注册表、目录、价格时效和数据文件 |
| `evidence validate` | 验证 evidence envelope |
| `evidence collect` | 从 JSON API/快照按 mapping spec 采集证据 |
| `evidence import` | 去重写入 JSONL 证据仓库 |
| `evidence latest` | 导出未过期的最新证据 |
| `registry validate` | 验证模型/来源注册表 |
| `registry sync` | 从 HTTPS 或本地文件同步并可固定 SHA-256 |
| `build` | 从 task、candidates 和 evidence store 构造 route input |
| `dispatch` | 安全地创建新的 Responses API 请求 |

兼容入口：

```bash
python scripts/router.py --input examples/route-input.json
python scripts/dispatch.py --route examples/route-result.json --context examples/context.json --dry-run
python scripts/collect_evidence.py --spec examples/collector-spec.json --output /tmp/evidence.json
```

---

## 路由输入

完整示例见 [`examples/route-input.json`](examples/route-input.json)。

```json
{
  "schema_version": "4.0",
  "as_of": "2026-08-07T00:00:00Z",
  "task": {
    "task_type": "coding.refactor.cross_file",
    "risk": "medium",
    "target_runtime": "api",
    "repo_id": "example-monorepo",
    "environment_id": "linux-ci",
    "estimated_context_tokens": 100000,
    "required_capabilities": ["functions"],
    "features": {
      "reasoning": {"bucket": "high", "confidence": 0.8},
      "cross_file": {"bucket": "high", "confidence": 0.9},
      "test_quality": {"bucket": "high", "confidence": 0.9}
    },
    "usage_estimate": {
      "input_tokens": 100000,
      "cached_input_tokens": 50000,
      "cache_write_tokens": 0,
      "output_tokens": 10000,
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

### 任务特征

可使用粗粒度 bucket，避免虚构精确小数：

```text
very_low | low | medium | high | very_high
```

11 个特征：

| 特征 | 含义 |
|---|---|
| `reasoning` | 推理复杂度 |
| `context` | 上下文压力 |
| `unfamiliarity` | 领域/代码陌生度 |
| `tools` | 工具编排强度 |
| `browser` | 浏览器或联网依赖 |
| `cross_file` | 跨文件/跨模块程度 |
| `test_quality` | 自动验证质量，越高越安全 |
| `detectability` | 错误是否容易发现，越高越安全 |
| `rollback` | 是否容易回滚，越高越安全 |
| `ambiguity` | 需求歧义 |
| `horizon` | 执行链长度和长期依赖 |

缺失维度使用低置信度默认值；低 task-vector confidence 会缩窄降级窗口，而不会制造虚假精度。

---

## Evidence Envelope

完整 Schema 见 [`docs/schema.zh-CN.md`](docs/schema.zh-CN.md)。严格模式推荐每条证据都包含：

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
  "raw_sha256": "...64 hex chars..."
}
```

### 证据元数据的安全默认值

- `match` 缺失或非法：降为 `proxy`；
- `observed_at` 缺失：严重降权，strict 拒绝；
- `sample_size` 缺失：降权，strict 拒绝；
- `ci_half_width` 缺失：降权；
- `snapshot_id/source_url/raw_sha256` 缺失：provenance 降权，strict 拒绝；
- `source_id` 未注册：拒绝；
- 超过来源 TTL：拒绝；
- 时间戳明显在未来：拒绝；
- exact evidence 的 model/effort 与候选不一致：拒绝。

### 验证证据

```bash
python scripts/select_model.py evidence validate \
  --input examples/evidence.json \
  --strict
```

---

## 自动采集和证据仓库

Select Model 不内置脆弱的网页 HTML scraper。它提供通用 JSON mapping adapter，可接稳定 API、下载后的快照或你自己的数据采集层。

采集定义见 [`examples/collector-spec.json`](examples/collector-spec.json)：

```bash
python scripts/select_model.py evidence collect \
  --spec examples/collector-spec.json \
  --output /tmp/collected.json \
  --store ~/.select-model/evidence.jsonl
```

安全控制：

- 远程地址必须是 HTTPS；
- URL 不允许内嵌用户名或密码；
- DNS 解析到 loopback、私网、link-local 等非公网地址时拒绝；
- 默认下载上限 20 MiB；
- 原始响应计算 SHA-256 并写入 envelope；
- JSONL store 使用文件锁和 `evidence_id` 去重。

导出未过期的最新证据：

```bash
python scripts/select_model.py evidence latest \
  --store ~/.select-model/evidence.jsonl \
  --output /tmp/latest-evidence.json
```

从仓库自动构造路由输入：

```bash
python scripts/select_model.py build \
  --task examples/task.json \
  --candidates examples/candidates.json \
  --evidence-store ~/.select-model/evidence.jsonl \
  --output /tmp/route-input.json
```

更完整的自动化方式见 [`docs/automation.zh-CN.md`](docs/automation.zh-CN.md)。

---

## 模型和来源注册表

### 模型注册表

[`config/models.json`](config/models.json) 管理：

- model ID 和 alias；
- 可用 runtime；
- API reasoning effort；
- effort capacity；
- context window 和 max output；
- capability；
- 输入、缓存、缓存写入和输出价格；
- 长上下文阈值与倍率；
- 价格来源和核验时间。

仓库附带的 2026-08-07 快照包含 GPT-5.6 Sol、Terra、Luna。模型事实和价格会变化，发布前或定期运行 `doctor` 并更新注册表。官方来源 URL 保存在每个模型的 pricing 配置中。

### 来源注册表

[`config/sources.json`](config/sources.json) 管理：

- 来源信任系数；
- TTL 和 freshness half-life；
- 样本规模曲线；
- 接受的指标；
- 对 coding、agent、research、writing、analysis 等任务族的相关性。

### 验证与安全同步

```bash
python scripts/select_model.py registry validate \
  --kind models \
  --input config/models.json

python scripts/select_model.py registry sync \
  --kind models \
  --location https://example.com/models.json \
  --sha256 EXPECTED_SHA256 \
  --output config/models.json
```

建议在生产中固定 SHA-256 或通过已审核的发布流程更新注册表。

---

## 个人历史与自动反馈

### 直接记录 attempt

```bash
python scripts/select_model.py record \
  --input examples/attempt.json \
  --history ~/.select-model/history.jsonl
```

### 从 route、response 和 outcome 自动记录

```bash
python scripts/select_model.py record \
  --route examples/route-result.json \
  --response examples/api-response.json \
  --outcome examples/outcome.json \
  --history ~/.select-model/history.jsonl \
  --hash-identifiers
```

建议记录：

```text
task_type
risk
repo_id / environment_id
model / snapshot / effort
input / cached / cache-write / output tokens
tool_cost
latency_seconds
first_pass_success
final_success
tests_passed
user_retry / fallback_triggered
quality_score
human_edit_minutes
```

`--hash-identifiers` 会在落盘前哈希 repo/environment。可通过环境变量 `SELECT_MODEL_HASH_SALT` 设置本地盐值：

```bash
export SELECT_MODEL_HASH_SALT='local-secret-not-in-git'
```

查看统计：

```bash
python scripts/select_model.py stats \
  --history ~/.select-model/history.jsonl
```

对于每日工作总结系统，可以在任务结束时自动写入 `outcome.json` 并调用上述 `record` 命令。无需保存完整 prompt 或代码正文，路由器只需要任务签名、结果、用量和环境标识。

---

## 安全 Dispatch

### 先 dry-run

```bash
python scripts/select_model.py dispatch \
  --route examples/route-result.json \
  --context examples/context.json \
  --dry-run
```

默认 dry-run 会脱敏字符串，只显示请求结构。确需查看完整内容时显式添加：

```bash
--show-content
```

### 实际请求

```bash
export OPENAI_API_KEY='...'
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --plan-prefix \
  --format text
```

### 自定义 endpoint 与密钥隔离

自定义网关必须显式允许，并使用独立环境变量。路由器拒绝把 `OPENAI_API_KEY` 发送到非官方 endpoint：

```bash
export INTERNAL_ROUTER_KEY='...'
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --endpoint https://gateway.example/v1/responses \
  --allow-custom-endpoint \
  --api-key-env INTERNAL_ROUTER_KEY
```

endpoint 必须是无 URL 凭据、无 fragment 的 HTTPS 地址。实时请求设有超时；采集器会对每一次重定向重新执行 DNS 与公网 IP 校验。

### Portable capability 如何判断

只从最终请求体推断：

| capability | 必须真实存在于 payload |
|---|---|
| `conversation` | `previous_response_id`，或实际发送的多轮消息/assistant 消息 |
| `files` | `input_file` content part |
| `mcp` | `tools[].type=mcp` |
| `web/browser` | web search 或 computer tool |
| `file_search` | file_search tool |
| `functions` | function tool |
| `instructions` | instructions 字段 |

以下状态不能由新 API 请求自动继承：

```text
repo / local_repo / workspace / tool_state /
browser_session / ide_state / shell_state
```

`file_ids` 默认只是一组声明；只有设置 `attach_file_ids=true` 并实际生成 `input_file` part 后才算 files portable。

当 required capability 缺失时，dispatch 默认拒绝。`--allow-context-loss` 可以用于明确接受普通风险的丢失；高风险和 critical 还必须添加：

```bash
--force-high-risk-context-loss
```

生产环境更推荐宿主原生的 context-preserving dispatcher，而不是这个新 API 请求 fallback。

---

## 路由结果解释

主要字段：

| 字段 | 含义 |
|---|---|
| `selected` | 最终选择及完整成本、证据和个人校准信息 |
| `fallback` | 一个更强配置，适合失败或不确定性升级 |
| `ranking` | 所有通过资格和证据检查的候选 |
| `rejected` | 资格或证据阶段拒绝的候选及原因 |
| `pareto_frontier` | 没有被成本、延迟、路由指数同时支配的候选 |
| `guards` | capability floor、source regret、最低来源数等保护阈值 |
| `route_stability` | task confidence、evidence confidence、top gap、来源重叠和 jackknife |
| `needs_more_context` | 高风险且稳定性不足时是否应补充信息 |
| `most_informative_missing_feature` | 最可能改变决策的缺失任务特征 |
| `handoff` | model、effort、plan、runtime、risk 和 required capabilities |
| `audit` | 输入指纹、注册表指纹和核验时间 |

不要把 `route_index=80` 解释为“80% 成功率”。

---

## Doctor 与质量检查

```bash
python scripts/select_model.py doctor
python scripts/select_model.py doctor --strict
```

检查内容包括：

- 模型和来源注册表 Schema；
- strict 模式下，价格是否超过 90 天、来源策略是否超过 180 天未核验；
- Python 版本；
- 开源仓库关键文件；
- 可选 history/evidence JSONL 损坏行；
- API Key 是否存在，仅作提示，不影响非 dispatch 功能。

完整测试：

```bash
make check
```

当前共有 **46 项单元与集成测试**，覆盖：

- 缺失证据元数据不得增信；
- 未知和过期来源拒绝；
- strict exact model、effort 与 snapshot binding；
- 单来源置信度/稳定性封顶；
- 来源内 rank aggregation；
- 负 token/价格拒绝、缓存写入与长上下文成本；
- file/history/host-state portability、自定义 endpoint 密钥隔离；
- 高风险资格 fail-closed；
- history 去重、时间衰减和 provisional 隔离；
- collector 重定向 SSRF 防护、evidence store 和 SHA-pinned registry sync。

---

## 项目结构

```text
select-model/
├── SKILL.md                    # ChatGPT Skill 控制面
├── README.md                   # 中文主文档
├── config/
│   ├── models.json             # 模型、能力、上下文、价格
│   └── sources.json            # 证据来源政策
├── select_model/
│   ├── router.py               # 路由主流程
│   ├── evidence.py             # envelope、来源内排名、稳健聚合
│   ├── task_profile.py         # 任务画像与不确定性
│   ├── cost.py                 # 任务级成本
│   ├── history.py              # 反馈、时间衰减、个人校准
│   ├── dispatch.py             # 安全 Responses API handoff
│   ├── automation.py           # collector/store/build/sync
│   ├── data/                   # 安装包内的注册表镜像
│   └── cli.py                  # CLI
├── scripts/
│   ├── release_check.py        # 开源发布总验收
│   ├── build_skill.py          # 确定性 Skill ZIP 构建
│   └── ...                     # 无安装入口和兼容脚本
├── examples/                   # 可运行示例
├── references/                 # Skill 按需加载的精简说明
├── docs/                       # 深入架构、Schema、自动化文档
├── tests/                      # unittest 测试集
└── .github/                    # CI、Issue 和 PR 模板
```

---

## 上传 GitHub 前

1. 创建 GitHub 仓库后，可在 `pyproject.toml` 增加真实的 `[project.urls]`。
2. 核对 `config/models.json` 和 `config/sources.json` 的事实、来源和日期。
3. 运行：

```bash
make check
make smoke
make release-check
```

4. 删除本地 history、evidence、API response 和任何敏感输出；这些默认已加入 `.gitignore`。
5. 创建仓库并推送：

```bash
git init
git add .
git commit -m "feat: open source select-model v4"
git branch -M main
git remote add origin git@github.com:YOUR_NAME/select-model.git
git push -u origin main
```

6. 在 GitHub 仓库设置中启用：
   - Actions；
   - Dependabot 或你偏好的依赖扫描；
   - Secret scanning；
   - Branch protection；
   - Private vulnerability reporting。

---

## 安全与隐私

- 不要把 `OPENAI_API_KEY`、真实 prompt、私有代码或内部 URL 提交到仓库。
- 推荐只保存任务特征、哈希标识、usage、结果和人工修改量。
- evidence snapshot 应保存来源 URL、时间和 SHA-256，便于审计。
- 自动 dispatch 前始终先 dry-run；高风险任务需要人工审批。
- 路由器不能保证模型结果正确，测试、回滚、权限隔离和结果审查仍由宿主负责。
- 详细策略见 [`SECURITY.md`](SECURITY.md)。

---

## 已知限制

- 默认 collector 面向 JSON API/快照，不负责通用 HTML 抓取或登录态页面。
- 来源内排名需要候选出现在可比 panel 中；只有一个候选时 panel 无法提供相对区分力。
- 个人校准在冷启动阶段只能显示 provisional，不能用于成功概率路由。
- 模型可用性、价格和 API 字段会变化，注册表需要持续维护。
- `dispatch` 不能替代宿主原生工作区迁移。
- 当前算法是保守启发式路由器，不是经过所有任务分布校准的最优策略证明。

---

## Roadmap

- 可插拔的官方/社区 source adapter 包；
- 模型注册表签名和发布工件校验；
- shadow routing 与离线反事实评估；
- 参数扰动和历史回放报告；
- document、vision、research、agent 等任务族策略插件；
- 宿主原生 `execute_selected_model` 接口参考实现；
- 可选 SQLite backend 和匿名化分析 dashboard。

---

## 贡献

请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md) 和 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。涉及安全问题时使用 [`SECURITY.md`](SECURITY.md) 中的私密报告方式。

## License

MIT，见 [`LICENSE`](LICENSE)。
