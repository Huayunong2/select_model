## 问题

说明要解决的问题和用户影响。

## 方案

说明关键设计、兼容性和未采用的替代方案。

## 安全不变量

- [ ] 缺失证据元数据不会提高权重
- [ ] 未知/过期来源不会进入正式路由
- [ ] 未直接平均异构 benchmark 原始值
- [ ] high/critical 资格检查保持 fail-closed
- [ ] 公共路由指数未被描述为任务成功率
- [ ] portable capability 可从最终请求体观察
- [ ] 自定义 endpoint 不会收到 `OPENAI_API_KEY`
- [ ] 不适用，原因已说明

## 测试

```text
粘贴 python scripts/release_check.py 的摘要
```

## 易变事实

涉及模型、价格、context、effort、capability 或来源政策时，请列出：

- 官方/原始来源：
- 核验日期：
- 注册表与示例是否同步：

## 隐私

- [ ] 未包含 API Key、真实私有 prompt、客户数据、内部 URL 或未脱敏历史
