"""Command-line interface for routing, automation, feedback, and dispatch."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .automation import (
    build_route_input,
    collect_from_mapping_spec,
    import_evidence,
    latest_evidence,
    load_evidence_store,
    sync_registry,
)
from .constants import (
    DEFAULT_EVIDENCE_STORE,
    DEFAULT_HISTORY_PATH,
    PROJECT_VERSION,
)
from .dispatch import dispatch_from_files
from .doctor import run_doctor
from .errors import SelectModelError, ValidationError
from .evidence import validate_evidence_envelope
from .history import build_attempt_from_artifacts, history_stats, load_history, record_attempt
from .registry import (
    load_model_registry,
    load_source_registry,
    validate_model_registry,
    validate_source_registry,
)
from .router import format_route_markdown, route
from .schemas import validate_context_manifest, validate_route_input
from .task import profile_text, task_pressures
from .utils import atomic_write_json, dump_json, load_json, load_json_any, parse_datetime, utc_now


def _json_print(value: Any, *, compact: bool = False) -> None:
    sys.stdout.write(dump_json(value, compact=compact))
    if compact:
        sys.stdout.write("\n")


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get("records"), list):
        return [item for item in value["records"] if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    raise ValidationError("expected an evidence object or array")


def _add_registry_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model-registry", help="Path to model registry JSON")
    parser.add_argument("--source-registry", help="Path to source registry JSON")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="select-model",
        description="Conservative model router with evidence, cost, and personal calibration.",
    )
    parser.add_argument("--version", action="version", version=PROJECT_VERSION)
    subcommands = parser.add_subparsers(dest="command", required=True)

    route_parser = subcommands.add_parser("route", help="Route a task")
    route_parser.add_argument("--input", required=True, help="Route input JSON")
    route_parser.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    route_parser.add_argument("--format", choices=("json", "markdown"), default="json")
    route_parser.add_argument("--compact", action="store_true")
    route_parser.add_argument("--output")
    _add_registry_paths(route_parser)

    profile_parser = subcommands.add_parser("profile", help="Profile a task without routing")
    profile_parser.add_argument("--input", required=True, help="Task JSON or route-input JSON")
    profile_parser.add_argument("--format", choices=("json", "text"), default="json")

    record_parser = subcommands.add_parser("record", help="Record a real execution outcome")
    record_source = record_parser.add_mutually_exclusive_group(required=True)
    record_source.add_argument("--input", help="Complete attempt JSON")
    record_source.add_argument("--route", help="Route result JSON; use with --response and --outcome")
    record_parser.add_argument("--response", help="Responses API result/usage JSON")
    record_parser.add_argument("--outcome", help="Observed outcome JSON")
    record_parser.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))
    record_parser.add_argument("--hash-identifiers", action="store_true", help="Hash repo/environment identifiers before writing")
    record_parser.add_argument("--hash-salt-env", default="SELECT_MODEL_HASH_SALT")

    stats_parser = subcommands.add_parser("stats", help="Summarize personal history")
    stats_parser.add_argument("--history", default=str(DEFAULT_HISTORY_PATH))

    doctor_parser = subcommands.add_parser("doctor", help="Validate installation and data files")
    doctor_parser.add_argument("--history")
    doctor_parser.add_argument("--evidence-store")
    doctor_parser.add_argument("--strict", action="store_true", help="Exit non-zero when required checks fail")
    _add_registry_paths(doctor_parser)

    evidence_parser = subcommands.add_parser("evidence", help="Evidence validation and storage")
    evidence_sub = evidence_parser.add_subparsers(dest="evidence_command", required=True)

    evidence_validate = evidence_sub.add_parser("validate", help="Validate evidence envelopes")
    evidence_validate.add_argument("--input", required=True)
    evidence_validate.add_argument("--strict", action="store_true")
    evidence_validate.add_argument("--source-registry")

    evidence_import = evidence_sub.add_parser("import", help="Import evidence into JSONL store")
    evidence_import.add_argument("--input", required=True)
    evidence_import.add_argument("--store", default=str(DEFAULT_EVIDENCE_STORE))
    evidence_import.add_argument("--allow-incomplete", action="store_true")
    evidence_import.add_argument("--source-registry")

    evidence_latest = evidence_sub.add_parser("latest", help="Export non-stale latest evidence")
    evidence_latest.add_argument("--store", default=str(DEFAULT_EVIDENCE_STORE))
    evidence_latest.add_argument("--as-of")
    evidence_latest.add_argument("--output")
    evidence_latest.add_argument("--source-registry")

    evidence_collect = evidence_sub.add_parser("collect", help="Collect from a declarative JSON mapping")
    evidence_collect.add_argument("--spec", required=True)
    evidence_collect.add_argument("--output", required=True)
    evidence_collect.add_argument("--store")
    evidence_collect.add_argument("--source-registry")
    evidence_collect.add_argument("--timeout", type=int, default=30)

    registry_parser = subcommands.add_parser("registry", help="Validate or securely sync registries")
    registry_sub = registry_parser.add_subparsers(dest="registry_command", required=True)

    registry_validate = registry_sub.add_parser("validate", help="Validate a registry")
    registry_validate.add_argument("--kind", choices=("models", "sources"), required=True)
    registry_validate.add_argument("--input", required=True)

    registry_sync = registry_sub.add_parser("sync", help="Sync an HTTPS/local registry with optional SHA pin")
    registry_sync.add_argument("--kind", choices=("models", "sources"), required=True)
    registry_sync.add_argument("--location", required=True)
    registry_sync.add_argument("--output", required=True)
    registry_sync.add_argument("--sha256")
    registry_sync.add_argument("--timeout", type=int, default=30)

    build_parser_cmd = subcommands.add_parser("build", help="Build route input from task, candidates, and evidence store")
    build_parser_cmd.add_argument("--task", required=True)
    build_parser_cmd.add_argument("--candidates", required=True)
    build_parser_cmd.add_argument("--evidence-store", default=str(DEFAULT_EVIDENCE_STORE))
    build_parser_cmd.add_argument("--as-of")
    build_parser_cmd.add_argument("--output", required=True)
    build_parser_cmd.add_argument("--source-registry")

    dispatch_parser = subcommands.add_parser("dispatch", help="Execute a handoff as a new Responses API request")
    dispatch_parser.add_argument("--route", required=True)
    dispatch_parser.add_argument("--context", required=True)
    dispatch_parser.add_argument("--prompt")
    dispatch_parser.add_argument("--prompt-file")
    dispatch_parser.add_argument("--plan-prefix", action="store_true")
    dispatch_parser.add_argument("--allow-context-loss", action="store_true")
    dispatch_parser.add_argument("--force-high-risk-context-loss", action="store_true")
    dispatch_parser.add_argument("--dry-run", action="store_true")
    dispatch_parser.add_argument("--show-content", action="store_true")
    dispatch_parser.add_argument("--output")
    dispatch_parser.add_argument("--format", choices=("json", "text"), default="json")
    dispatch_parser.add_argument("--endpoint", default="https://api.openai.com/v1/responses")
    dispatch_parser.add_argument("--allow-custom-endpoint", action="store_true")
    dispatch_parser.add_argument(
        "--api-key-env",
        default="OPENAI_API_KEY",
        help="Environment variable containing the endpoint API key; custom endpoints require a dedicated name",
    )
    dispatch_parser.add_argument("--timeout", type=int, default=600)
    dispatch_parser.add_argument("--model-registry")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "route":
            data = load_json(args.input)
            errors = validate_route_input(data)
            if errors:
                raise ValidationError("; ".join(errors))
            history_result = load_history(args.history)
            models = load_model_registry(args.model_registry)
            sources = load_source_registry(args.source_registry)
            result = route(
                data,
                history=history_result["rows"],
                model_registry=models,
                source_registry=sources,
            )
            if history_result["warnings"]:
                result.setdefault("warnings", []).extend(history_result["warnings"])
            output_text = format_route_markdown(result) if args.format == "markdown" else dump_json(result, compact=args.compact)
            if args.output:
                Path(args.output).write_text(output_text, encoding="utf-8")
            else:
                sys.stdout.write(output_text)
                if args.compact and not output_text.endswith("\n"):
                    sys.stdout.write("\n")
            return 0

        if args.command == "profile":
            value = load_json(args.input)
            task = value.get("task") if isinstance(value.get("task"), dict) else value
            pressure = task_pressures(task)
            if args.format == "text":
                print(profile_text(pressure))
            else:
                _json_print(pressure)
            return 0

        if args.command == "record":
            if args.input:
                attempt = load_json(args.input)
            else:
                if not args.response or not args.outcome:
                    raise ValidationError("--route requires --response and --outcome")
                attempt = build_attempt_from_artifacts(
                    load_json(args.route),
                    load_json(args.response),
                    load_json(args.outcome),
                )
            import os
            result = record_attempt(
                attempt,
                args.history,
                hash_identifiers=args.hash_identifiers,
                hash_salt=os.environ.get(args.hash_salt_env, ""),
            )
            _json_print(result)
            return 0

        if args.command == "stats":
            loaded = load_history(args.history)
            result = history_stats(loaded["rows"])
            result["history_warnings"] = loaded["warnings"]
            _json_print(result)
            return 0

        if args.command == "doctor":
            result = run_doctor(
                model_registry_path=args.model_registry,
                source_registry_path=args.source_registry,
                history_path=args.history,
                evidence_store_path=args.evidence_store,
                strict=args.strict,
            )
            _json_print(result)
            return 2 if args.strict and not result["healthy"] else 0

        if args.command == "evidence":
            sources = load_source_registry(getattr(args, "source_registry", None))
            if args.evidence_command == "validate":
                results: list[dict[str, Any]] = []
                all_valid = True
                for index, record in enumerate(_records(load_json_any(args.input))):
                    errors, warnings = validate_evidence_envelope(record, sources, strict=args.strict)
                    results.append({"index": index, "valid": not errors, "errors": errors, "warnings": warnings})
                    all_valid = all_valid and not errors
                _json_print({"valid": all_valid, "records": results})
                return 0 if all_valid else 2
            if args.evidence_command == "import":
                result = import_evidence(
                    load_json_any(args.input),
                    args.store,
                    sources,
                    strict=not args.allow_incomplete,
                )
                _json_print(result)
                return 0 if not result["rejected"] else 2
            if args.evidence_command == "latest":
                loaded = load_evidence_store(args.store)
                as_of = parse_datetime(args.as_of) if args.as_of else utc_now()
                if as_of is None:
                    raise ValidationError("--as-of must be ISO-8601")
                records = latest_evidence(loaded["records"], sources, as_of=as_of)
                output = {"as_of": as_of.isoformat(), "records": records, "warnings": loaded["warnings"]}
                if args.output:
                    atomic_write_json(args.output, output)
                else:
                    _json_print(output)
                return 0
            if args.evidence_command == "collect":
                result = collect_from_mapping_spec(
                    load_json(args.spec),
                    sources,
                    timeout_seconds=args.timeout,
                )
                atomic_write_json(args.output, result)
                imported = None
                if args.store:
                    imported = import_evidence(result, args.store, sources, strict=True)
                _json_print(
                    {
                        "output": args.output,
                        "records": len(result["records"]),
                        "skipped": result["skipped"],
                        "import": imported,
                    }
                )
                return 0

        if args.command == "registry":
            if args.registry_command == "validate":
                registry = load_json(args.input)
                warnings = validate_model_registry(registry) if args.kind == "models" else validate_source_registry(registry)
                _json_print({"valid": True, "kind": args.kind, "warnings": warnings})
                return 0
            result = sync_registry(
                args.kind,
                args.location,
                args.output,
                expected_sha256=args.sha256,
                timeout_seconds=args.timeout,
            )
            _json_print(result)
            return 0

        if args.command == "build":
            task_value = load_json(args.task)
            task = task_value.get("task") if isinstance(task_value.get("task"), dict) else task_value
            candidate_value = load_json_any(args.candidates)
            if isinstance(candidate_value, dict):
                candidates = candidate_value.get("candidates")
            else:
                candidates = candidate_value
            if not isinstance(candidates, list):
                raise ValidationError("candidate file must contain an array or {candidates: [...]} object")
            sources = load_source_registry(args.source_registry)
            loaded = load_evidence_store(args.evidence_store)
            as_of_dt = parse_datetime(args.as_of) if args.as_of else utc_now()
            if as_of_dt is None:
                raise ValidationError("--as-of must be ISO-8601")
            evidence = latest_evidence(loaded["records"], sources, as_of=as_of_dt)
            result = build_route_input(
                task,
                candidates,
                evidence,
                as_of=as_of_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            )
            atomic_write_json(args.output, result)
            _json_print({"output": args.output, "candidates": len(candidates), "evidence": len(evidence)})
            return 0

        if args.command == "dispatch":
            context = load_json(args.context)
            context_errors = validate_context_manifest(context)
            if context_errors:
                raise ValidationError("; ".join(context_errors))
            prompt_override = args.prompt
            if args.prompt_file:
                prompt_override = Path(args.prompt_file).read_text(encoding="utf-8")
            models = load_model_registry(args.model_registry)
            result = dispatch_from_files(
                args.route,
                args.context,
                prompt_override=prompt_override,
                plan_prefix=args.plan_prefix,
                allow_context_loss=args.allow_context_loss,
                force_high_risk_context_loss=args.force_high_risk_context_loss,
                dry_run=args.dry_run,
                show_content=args.show_content,
                output_path=args.output,
                endpoint=args.endpoint,
                allow_custom_endpoint=args.allow_custom_endpoint,
                timeout_seconds=args.timeout,
                api_key_env=args.api_key_env,
                model_registry=models,
            )
            if args.format == "text" and not args.dry_run:
                print(result.get("text", ""))
            else:
                _json_print(result)
            return 0

        parser.error("unknown command")
        return 2
    except (SelectModelError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
