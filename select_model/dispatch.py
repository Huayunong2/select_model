"""Safe Responses API dispatcher for portable route handoffs.

This module always creates a *new* API request. It cannot inherit an IDE process,
local repository, browser login, connector session, or hidden conversation state.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .constants import HOST_ONLY_CAPABILITIES, PASSTHROUGH_RESPONSE_FIELDS
from .errors import DispatchError, ValidationError
from .registry import load_model_registry, resolve_model_id
from .utils import load_json, redact

DEFAULT_RESPONSES_URL = "https://api.openai.com/v1/responses"
_API_KEY_ENV_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_endpoint(endpoint: str, *, allow_custom_endpoint: bool) -> bool:
    """Validate the endpoint and return whether it is custom."""
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise DispatchError("dispatch endpoint must use HTTPS and include a hostname")
    if parsed.username or parsed.password:
        raise DispatchError("dispatch endpoint must not contain embedded credentials")
    if parsed.fragment:
        raise DispatchError("dispatch endpoint must not contain a URL fragment")
    is_custom = endpoint.rstrip("/") != DEFAULT_RESPONSES_URL
    if is_custom and not allow_custom_endpoint:
        raise DispatchError("custom endpoint requires allow_custom_endpoint=true")
    return is_custom


def _handoff(route_result: dict[str, Any]) -> dict[str, Any]:
    handoff = route_result.get("handoff", route_result)
    if not isinstance(handoff, dict):
        raise ValidationError("route JSON has no handoff object")
    return handoff


def _text_message(role: str, text: str) -> dict[str, Any]:
    return {
        "role": role,
        "content": [{"type": "input_text", "text": text}],
    }


def _normalize_history(history: Any) -> list[dict[str, Any]]:
    if history is None:
        return []
    if not isinstance(history, list):
        raise ValidationError("input_history must be an array")
    output: list[dict[str, Any]] = []
    for index, item in enumerate(history):
        if not isinstance(item, dict) or not isinstance(item.get("role"), str):
            raise ValidationError(f"input_history[{index}] must contain role")
        output.append(item)
    return output


def _append_files_to_input(input_value: Any, file_ids: list[str]) -> Any:
    file_parts = [{"type": "input_file", "file_id": file_id} for file_id in file_ids]
    if isinstance(input_value, str):
        return [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": input_value}, *file_parts],
            }
        ]
    if isinstance(input_value, list):
        output = list(input_value)
        output.append({"role": "user", "content": file_parts})
        return output
    raise ValidationError("cannot attach files without a string or message-array input")


def _build_input(context: dict[str, Any], prompt_override: str | None) -> tuple[Any, list[str]]:
    warnings: list[str] = []
    if prompt_override is not None:
        return prompt_override, warnings

    history = _normalize_history(context.get("input_history"))
    current = context.get("input")
    if current is None and isinstance(context.get("prompt"), str):
        current = context["prompt"]

    if history:
        payload_input = list(history)
        if isinstance(current, str):
            payload_input.append(_text_message("user", current))
        elif isinstance(current, list):
            payload_input.extend(current)
        elif current is not None:
            raise ValidationError("context.input must be a string or message array")
    elif current is not None:
        payload_input = current
    else:
        raise ValidationError("context requires input, prompt, or input_history")

    raw_file_ids = context.get("file_ids")
    file_ids = [str(item) for item in raw_file_ids] if isinstance(raw_file_ids, list) else []
    if file_ids:
        if bool(context.get("attach_file_ids")):
            payload_input = _append_files_to_input(payload_input, file_ids)
        else:
            warnings.append(
                "file_ids were declared but not attached; set attach_file_ids=true to emit input_file parts"
            )
    return payload_input, warnings


def _walk_has_file(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"input_file", "file"} and value.get("file_id"):
            return True
        return any(_walk_has_file(item) for item in value.values())
    if isinstance(value, list):
        return any(_walk_has_file(item) for item in value)
    return False


def _message_count(input_value: Any) -> tuple[int, bool]:
    if not isinstance(input_value, list):
        return 0, False
    messages = [item for item in input_value if isinstance(item, dict) and isinstance(item.get("role"), str)]
    has_assistant = any(str(item.get("role")).lower() == "assistant" for item in messages)
    return len(messages), has_assistant


def _tool_types(tools: Any) -> set[str]:
    if not isinstance(tools, list):
        return set()
    return {
        str(tool.get("type", "")).lower()
        for tool in tools
        if isinstance(tool, dict) and tool.get("type")
    }


def observable_capabilities(payload: dict[str, Any]) -> set[str]:
    """Infer portability only from fields present in the outgoing request."""
    capabilities: set[str] = set()
    if payload.get("instructions"):
        capabilities.add("instructions")
    if payload.get("previous_response_id"):
        capabilities.add("conversation")

    message_count, has_assistant = _message_count(payload.get("input"))
    if message_count >= 2 or has_assistant:
        capabilities.add("conversation")
    if _walk_has_file(payload.get("input")):
        capabilities.add("files")

    tool_types = _tool_types(payload.get("tools"))
    if "mcp" in tool_types:
        capabilities.add("mcp")
    if tool_types & {"web_search", "web_search_preview"}:
        capabilities.update({"web", "browser", "web_search"})
    if tool_types & {"computer", "computer_use_preview"}:
        capabilities.update({"computer", "browser"})
    if "file_search" in tool_types:
        capabilities.add("file_search")
    if tool_types & {"hosted_shell", "apply_patch"}:
        capabilities.add("shell")
    if tool_types & {"function", "functions"}:
        capabilities.add("functions")
    return capabilities


def build_request(
    route_result: dict[str, Any],
    context: dict[str, Any],
    *,
    prompt_override: str | None = None,
    plan_prefix: bool = False,
    model_registry: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(context, dict):
        raise ValidationError("context manifest must be an object")
    for field in ("required_capabilities", "portable_capabilities", "file_ids", "input_history"):
        if field in context and not isinstance(context[field], list):
            raise ValidationError(f"{field} must be an array")

    handoff = _handoff(route_result)
    registry = model_registry or load_model_registry()
    requested_model = str(handoff.get("model", ""))
    model = resolve_model_id(requested_model, registry)
    model_entry = registry.get("models", {}).get(model)
    if not isinstance(model_entry, dict):
        raise DispatchError(f"model is not allowlisted by the registry: {requested_model}")

    effort = str(handoff.get("reasoning_effort", "medium")).lower()
    allowed_efforts = {str(item).lower() for item in model_entry.get("api_efforts", [])}
    if effort not in allowed_efforts:
        raise DispatchError(f"unsupported API reasoning effort for {model}: {effort}")

    product_mode = str(handoff.get("product_mode", "standard")).lower()
    if product_mode not in {"", "standard", "api"}:
        raise DispatchError(
            f"product_mode={product_mode!r} is host-specific and cannot be encoded as reasoning.effort"
        )

    payload: dict[str, Any] = {}
    for field in PASSTHROUGH_RESPONSE_FIELDS:
        if field in context:
            payload[field] = context[field]

    payload_input, warnings = _build_input(context, prompt_override)
    payload["input"] = payload_input

    if plan_prefix and bool(handoff.get("plan")):
        instruction = "Before executing, form a concise plan, verify key assumptions, then carry it out."
        existing = payload.get("instructions")
        payload["instructions"] = f"{existing}\n{instruction}" if existing else instruction

    reasoning: dict[str, Any] = {"effort": effort}
    reasoning_context = context.get("reasoning_context")
    if reasoning_context in {"auto", "all_turns", "current_turn"}:
        reasoning["context"] = reasoning_context
    payload["model"] = model
    payload["reasoning"] = reasoning

    required = {
        str(item).lower()
        for item in [
            *(context.get("required_capabilities") or []),
            *(handoff.get("required_capabilities") or []),
        ]
        if str(item).strip()
    }
    observable = observable_capabilities(payload)
    declared = {
        str(item).lower()
        for item in (context.get("portable_capabilities") or [])
        if str(item).strip()
    }
    declared_but_unobserved = sorted(declared - observable)
    if declared_but_unobserved:
        warnings.append(
            "declared portable capabilities are not observable in the outgoing payload: "
            + ", ".join(declared_but_unobserved)
        )

    host_only_required = sorted(required & HOST_ONLY_CAPABILITIES)
    missing = sorted(required - observable)
    for capability in host_only_required:
        warnings.append(
            f"{capability} is host state and cannot be inherited by a new Responses API request"
        )

    report = {
        "required": sorted(required),
        "observable_in_request": sorted(observable),
        "declared": sorted(declared),
        "declared_but_unobserved": declared_but_unobserved,
        "missing": missing,
        "host_only_required": host_only_required,
        "warnings": warnings,
        "safe": not missing,
        "risk": str(handoff.get("risk", "medium")).lower(),
    }
    return payload, report


def _extract_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in response.get("output") or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                parts.append(content["text"])
    return "\n".join(parts)


def dispatch(
    route_result: dict[str, Any],
    context: dict[str, Any],
    *,
    prompt_override: str | None = None,
    plan_prefix: bool = False,
    allow_context_loss: bool = False,
    force_high_risk_context_loss: bool = False,
    dry_run: bool = False,
    show_content: bool = False,
    output_path: str | Path | None = None,
    endpoint: str = DEFAULT_RESPONSES_URL,
    allow_custom_endpoint: bool = False,
    timeout_seconds: int = 600,
    api_key_env: str = "OPENAI_API_KEY",
    model_registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload, report = build_request(
        route_result,
        context,
        prompt_override=prompt_override,
        plan_prefix=plan_prefix,
        model_registry=model_registry,
    )

    if report["missing"] and not allow_context_loss:
        raise DispatchError(
            "required context is not present in the outgoing request: "
            + ", ".join(report["missing"])
        )
    if report["missing"] and report["risk"] in {"high", "critical"} and not force_high_risk_context_loss:
        raise DispatchError(
            "high/critical-risk context loss requires the separate force_high_risk_context_loss override"
        )

    is_custom_endpoint = _validate_endpoint(
        endpoint,
        allow_custom_endpoint=allow_custom_endpoint,
    )
    if timeout_seconds <= 0:
        raise DispatchError("timeout_seconds must be positive")

    if dry_run:
        return {
            "request": payload if show_content else redact(payload),
            "context_report": report,
            "dry_run": True,
        }

    if not _API_KEY_ENV_PATTERN.fullmatch(api_key_env):
        raise DispatchError("api_key_env is not a valid environment variable name")
    if is_custom_endpoint and api_key_env == "OPENAI_API_KEY":
        raise DispatchError(
            "refusing to send OPENAI_API_KEY to a custom endpoint; "
            "use a dedicated api_key_env"
        )
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise DispatchError(f"{api_key_env} is not set")

    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise DispatchError(f"OpenAI API HTTP {exc.code}: {body[:1000]}") from exc
    except urllib.error.URLError as exc:
        raise DispatchError(f"OpenAI API request failed: {exc}") from exc

    if output_path:
        Path(output_path).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return {
        "response": result,
        "text": _extract_text(result),
        "context_report": report,
        "dry_run": False,
    }


def dispatch_from_files(
    route_path: str | Path,
    context_path: str | Path,
    **kwargs: Any,
) -> dict[str, Any]:
    return dispatch(load_json(route_path), load_json(context_path), **kwargs)
