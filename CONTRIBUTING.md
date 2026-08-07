# 贡献指南

感谢参与 Select Model。提交变更前请先阅读以下约定。

## 开发环境

需要 Python 3.11 或更高版本，无运行时第三方依赖。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
make check
```

## 变更原则

1. 不得把公共 benchmark 转换成用户任务成功率。
2. 新证据来源必须进入 `config/sources.json`，并说明量纲、时效、样本与适用任务族。
3. 缺失元数据不得提高信任权重。
4. 高风险资格检查必须 fail-closed。
5. 新的自动执行能力必须证明上下文真实进入最终请求。
6. 新增或修复行为必须有测试；安全不变量优先使用回归测试固定。
7. 模型、价格、上下文和 capability 等易变事实必须放入注册表，不得散落硬编码。
8. 远程 URL、重定向和自定义 endpoint 必须有 SSRF、凭据和密钥隔离测试。
9. 路由审计必须同时固定模型注册表与来源注册表的内容指纹。

## 提交前检查

```bash
make check
python scripts/select_model.py route \
  --input examples/route-input.json \
  --history /tmp/select-model-history.jsonl \
  --format markdown
```

## Pull Request

PR 描述需要包含问题、方案、风险、兼容性、测试结果，以及注册表事实的来源和核验日期。避免在提交中包含 API Key、真实 prompt、私有仓库名和未经脱敏的历史记录。
