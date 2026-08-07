# 更新日志

本项目遵循语义化版本。

## 4.0.0 - 2026-08-07

### 新增

- 版本化模型注册表与证据来源注册表。
- 来源内排名、跨来源稳健聚合和来源移除稳定性测试。
- `strict`、`balanced`、`explore` 三档资格策略，高风险自动严格模式。
- 通用 JSON 证据采集器、JSONL 证据仓库、去重导入和过期过滤。
- 任务级缓存读取、缓存写入、长上下文、工具成本计算。
- 时间衰减、模型快照、任务族、仓库和环境隔离的个人校准。
- 从 route、API response、outcome 自动生成反馈记录。
- 安全的 Responses API handoff、请求体可观测能力检查和默认脱敏 dry-run。
- 中文 README、架构文档、Schema 文档、自动化文档、贡献与安全指南。
- 46 项单元与集成测试、GitHub Actions 和发布验收脚本。

### 修复

- 缺少证据时间、样本、区间或匹配类型时不再获得更高权重。
- 未知来源不再进入路由。
- 过期证据不再依靠最低权重永久存活。
- 高风险下未知 capability、runtime、context window 不再 fail-open。
- `file_ids`、`input_history` 和声明式 portable capability 不再产生上下文迁移假阳性。
- 缓存写入价格缺失时不再按普通输入价格低估。
- 精确 model+effort 证据不再被通用 effort penalty 完整重复惩罚。
- 单一弱来源不再产生高 route stability。
- 精确证据的模型 snapshot 不一致时不再进入对应候选。
- collector 的每一次重定向都重新校验协议、DNS 和公网 IP。
- 自定义 endpoint 不再允许复用 `OPENAI_API_KEY`。
- 发布安装检查改为离线、无缓存并带确定性超时。
