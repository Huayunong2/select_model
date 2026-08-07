# Execution, context portability, and API safety

Use this reference when the user asks to execute the selected route, switch models, preserve context, or automate dispatch.

## Default

Execution is off by default. The Skill recommends a route and returns a handoff; it must not silently create a second paid request.

A ChatGPT Skill cannot grant itself control of the model already running the current turn.

## Preferred host contract

Use a host-native action when available:

```text
execute_selected_model(model, effort, execution_context_ref)
```

The opaque host reference should preserve:

- repository/worktree and branch;
- conversation and applicable instructions;
- uploaded files;
- tools, MCP/apps/connectors, and credentials;
- browser/computer authenticated session;
- shell and tool state;
- approvals, permission boundaries, and audit IDs.

## Responses API fallback

`dispatch` starts a new request. It does not inherit IDE, repo, browser login, connector state, hidden instructions, or local processes.

Run a dry-run first:

```bash
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --dry-run
```

Dry-run output is redacted unless `--show-content` is explicitly supplied.

## Context manifest

```json
{
  "required_capabilities": ["conversation", "files", "functions"],
  "previous_response_id": "resp_...",
  "instructions": "Preserve the project's constraints and tests.",
  "input_history": [
    {"role": "user", "content": "Earlier request"},
    {"role": "assistant", "content": "Earlier result"}
  ],
  "input": "Continue the task.",
  "file_ids": ["file_..."],
  "attach_file_ids": true,
  "tools": [
    {"type": "function", "name": "run_tests", "parameters": {}}
  ]
}
```

## Observable portability

Count a capability only when it is present in the final outgoing request:

| Capability | Observable request evidence |
|---|---|
| conversation | `previous_response_id`, multiple sent messages, or an assistant message |
| files | actual `input_file` part |
| functions | function tool |
| mcp | MCP tool |
| web/browser | web search or computer tool |
| file_search | file_search tool |
| instructions | instructions field |

`portable_capabilities` is an audit declaration only. `file_ids` alone are not attachments. Set `attach_file_ids=true` to emit `input_file` parts.

Host-only state such as `repo`, `workspace`, `tool_state`, `browser_session`, `ide_state`, and `shell_state` cannot be declared into existence.

## Context-loss protection

If required context is missing, dispatch refuses by default.

For low/medium risk, an explicit `--allow-context-loss` may override the refusal when the user understands the consequences.

For high/critical risk, the same override is insufficient. The caller must also provide:

```bash
--force-high-risk-context-loss
```

Do not use either flag for tasks whose correctness depends on missing state.

## Product modes and effort

Use the model registry's API effort allowlist. Keep product modes separate:

```text
reasoning_effort: none | low | medium | high | xhigh | max
product_mode: standard or host-specific metadata
```

A host-specific mode cannot be sent as API `reasoning.effort`.

## Endpoint and credentials

Default endpoint:

```text
https://api.openai.com/v1/responses
```

Custom endpoints require:

- HTTPS;
- no embedded URL credentials or fragment;
- explicit `--allow-custom-endpoint`;
- a dedicated API-key environment variable.

Example:

```bash
export INTERNAL_GATEWAY_KEY='...'
python scripts/select_model.py dispatch \
  --route route-result.json \
  --context context.json \
  --endpoint https://gateway.example/v1/responses \
  --allow-custom-endpoint \
  --api-key-env INTERNAL_GATEWAY_KEY
```

The dispatcher refuses to send `OPENAI_API_KEY` to a custom endpoint.

## Live execution controls

Recommended host controls:

- model/effort allowlist;
- request and daily spend caps;
- approval for side effects and high-risk context loss;
- idempotency/fallback checks;
- dry-run and payload audit;
- route ID, response ID, usage, latency, retries, tests, fallback, and outcome logging;
- feedback through `record`.
