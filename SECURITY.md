# 安全策略

## 支持范围

当前维护分支为 `4.x`。安全修复优先发布到最新 `4.x` 版本。

## 报告漏洞

请通过 GitHub Security Advisory 私下报告。不要在公开 Issue 中提交 API Key、内部 URL、真实对话、私有仓库内容或可复现的敏感凭据。

报告应包含影响、复现步骤、受影响版本、建议修复和是否已被利用。维护者确认前请避免公开细节。

## 安全边界

- `dispatch` 创建新的 Responses API 请求，不继承本地仓库、IDE、浏览器登录或 connector 状态。
- `portable_capabilities` 只是声明，不能替代最终请求体中的可观测数据。
- 远程采集和注册表同步只允许 HTTPS，并拒绝凭据 URL 和解析到非公网 IP 的主机；每一次重定向都会重新校验。
- API Key 只从环境变量读取，永远不要写入 Skill、配置、示例或历史文件。
- 自定义 endpoint 必须使用独立密钥变量；`OPENAI_API_KEY` 只允许发送到官方 Responses API。
- `--allow-context-loss` 不足以绕过高风险保护；高风险还需要独立强制参数。
- dry-run 默认脱敏，只有显式 `--show-content` 才显示请求内容。

本项目提供路由建议，不构成安全、法律、医疗或财务保证。高风险任务仍需人工审批、测试、回滚和审计。
