#!/usr/bin/env python3
"""Run the open-source and Skill release gate with no third-party dependencies."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from select_model.doctor import run_doctor  # noqa: E402
from select_model.registry import (  # noqa: E402
    validate_model_registry,
    validate_source_registry,
)
from select_model.utils import load_json  # noqa: E402

REQUIRED_FILES = [
    "SKILL.md",
    "agents/openai.yaml",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    "pyproject.toml",
    "config/models.json",
    "config/sources.json",
    "select_model/data/models.json",
    "select_model/data/sources.json",
    "docs/architecture.zh-CN.md",
    "docs/schema.zh-CN.md",
    "docs/automation.zh-CN.md",
    ".github/workflows/ci.yml",
    "tests/test_router.py",
]
TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".txt"}
SKIP_DIRS = {".git", ".venv", "venv", "build", "dist", "__pycache__"}
SECRET_PATTERNS = {
    "OpenAI-style API key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks.append({"name": name, "ok": ok, "detail": detail})
        marker = "PASS" if ok else "FAIL"
        print(f"[{marker}] {name}" + (f": {detail}" if detail else ""))

    @property
    def ok(self) -> bool:
        return all(item["ok"] for item in self.checks)


def _run(
    command: list[str],
    *,
    cwd: Path = ROOT,
    env: dict[str, str] | None = None,
    timeout_seconds: int = 120,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged["PYTHONDONTWRITEBYTECODE"] = "1"
    if env:
        merged.update(env)
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=merged,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        output += f"\ncommand timed out after {timeout_seconds}s"
        return subprocess.CompletedProcess(command, 124, stdout=output)


def _project_files() -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        files.append(path)
    return files


def _check_skill_frontmatter() -> tuple[bool, str]:
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return False, "frontmatter opening delimiter is missing"
    end = text.find("\n---\n", 4)
    if end < 0:
        return False, "frontmatter closing delimiter is missing"
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            return False, f"invalid frontmatter line: {line}"
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    if set(fields) != {"name", "description"}:
        return False, "frontmatter must contain only name and description"
    if fields["name"] != "select-model":
        return False, "name must be select-model"
    if len(fields["description"]) < 80:
        return False, "description is too short to route reliably"
    if text.count("\n") > 500:
        return False, "SKILL.md exceeds 500 lines"
    return True, f"{text.count(chr(10)) + 1} lines"


def _check_python_syntax() -> tuple[bool, str]:
    checked = 0
    for path in _project_files():
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            return False, f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}"
        checked += 1
    return True, f"{checked} Python files"


def _check_json() -> tuple[bool, str]:
    checked = 0
    for path in _project_files():
        if path.suffix != ".json":
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"{path.relative_to(ROOT)}:{exc.lineno}:{exc.colno}"
        checked += 1
    return True, f"{checked} JSON files"


def _check_registry_mirrors() -> tuple[bool, str]:
    pairs = [
        (ROOT / "config/models.json", ROOT / "select_model/data/models.json"),
        (ROOT / "config/sources.json", ROOT / "select_model/data/sources.json"),
    ]
    for public, packaged in pairs:
        if public.read_bytes() != packaged.read_bytes():
            return False, f"{public.relative_to(ROOT)} differs from packaged mirror"
    return True, "public and installed-package registries match"


def _check_markdown_links() -> tuple[bool, str]:
    checked = 0
    for path in _project_files():
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip().split()[0].strip("<>")
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            destination = (path.parent / target).resolve()
            try:
                destination.relative_to(ROOT.resolve())
            except ValueError:
                return False, f"link escapes repository: {path.relative_to(ROOT)} -> {target}"
            if not destination.exists():
                return False, f"broken link: {path.relative_to(ROOT)} -> {target}"
            checked += 1
    return True, f"{checked} local links"


def _check_secrets() -> tuple[bool, str]:
    scanned = 0
    for path in _project_files():
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"Makefile", ".gitignore", ".editorconfig"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                return False, f"possible {label} in {path.relative_to(ROOT)}"
        scanned += 1
    return True, f"{scanned} text files"


def _check_generated_files() -> tuple[bool, str]:
    unwanted = []
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT)
        generated_directory = any(
            part in {"__pycache__", ".pytest_cache", "build", "dist"}
            or part.endswith(".egg-info")
            for part in relative.parts
        )
        if generated_directory or path.suffix in {".pyc", ".pyo"}:
            unwanted.append(str(relative))
    if unwanted:
        return False, ", ".join(unwanted[:5])
    return True, "no generated build or bytecode files"


def _run_tests() -> tuple[bool, str]:
    result = _run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        timeout_seconds=180,
    )
    if result.returncode:
        return False, result.stdout[-2000:].strip()
    match = re.search(r"Ran (\d+) tests?", result.stdout)
    return True, f"{match.group(1) if match else 'all'} tests passed"


def _run_smoke(temp: Path) -> tuple[bool, str]:
    route_output = temp / "route.json"
    commands = [
        [
            sys.executable,
            "scripts/select_model.py",
            "doctor",
            "--strict",
        ],
        [
            sys.executable,
            "scripts/select_model.py",
            "evidence",
            "validate",
            "--input",
            "examples/evidence.json",
            "--strict",
        ],
        [
            sys.executable,
            "scripts/select_model.py",
            "route",
            "--input",
            "examples/route-input.json",
            "--history",
            str(temp / "history.jsonl"),
            "--output",
            str(route_output),
        ],
        [
            sys.executable,
            "scripts/select_model.py",
            "dispatch",
            "--route",
            str(route_output),
            "--context",
            "examples/context.json",
            "--dry-run",
        ],
    ]
    for command in commands:
        result = _run(command, timeout_seconds=60)
        if result.returncode:
            return False, f"{' '.join(command[1:])}\n{result.stdout[-1600:].strip()}"
    return True, f"{len(commands)} CLI smoke commands"


def _check_install(temp: Path) -> tuple[bool, str]:
    target = temp / "install"
    source = temp / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "venv",
            "build",
            "dist",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
            "*.egg-info",
            "*.pyc",
            "*.pyo",
        ),
    )
    result = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--no-cache-dir",
            "--target",
            str(target),
            str(source),
        ],
        cwd=temp,
        env={"PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INDEX": "1"},
        timeout_seconds=90,
    )
    if result.returncode:
        return False, result.stdout[-2000:].strip()
    probe = _run(
        [
            sys.executable,
            "-c",
            (
                "from select_model.registry import load_model_registry, load_source_registry; "
                "assert load_model_registry()['models']; "
                "assert load_source_registry()['sources']; print('installed registries ok')"
            ),
        ],
        cwd=temp,
        env={"PYTHONPATH": str(target)},
        timeout_seconds=30,
    )
    if probe.returncode:
        return False, probe.stdout[-1200:].strip()
    return True, "isolated install can load packaged registries"


def _check_package(temp: Path) -> tuple[bool, str]:
    output = temp / "skill.zip"
    result = _run(
        [
            sys.executable,
            "scripts/build_skill.py",
            "--root",
            str(ROOT),
            "--output",
            str(output),
        ],
        timeout_seconds=60,
    )
    if result.returncode:
        return False, result.stdout[-1200:].strip()
    if not output.exists() or output.stat().st_size > 25 * 1024 * 1024:
        return False, "skill.zip is missing or exceeds 25 MiB"
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        prefix = ROOT.name + "/"
        for required in (
            "SKILL.md",
            "agents/openai.yaml",
            "README.md",
            "examples/api-response.json",
            "examples/route-result.json",
        ):
            if prefix + required not in names:
                return False, f"package is missing {required}"
        if any(
            "__pycache__" in name
            or name.endswith((".pyc", ".pyo"))
            or ".egg-info/" in name
            or "/build/" in name
            for name in names
        ):
            return False, "package contains generated build or bytecode files"
    return True, f"{len(names)} files, {output.stat().st_size} bytes"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--json-output")
    args = parser.parse_args()

    report = Report()
    missing = [item for item in REQUIRED_FILES if not (ROOT / item).exists()]
    report.add("required files", not missing, ", ".join(missing) if missing else f"{len(REQUIRED_FILES)} files")

    for name, function in (
        ("Skill frontmatter", _check_skill_frontmatter),
        ("Python syntax", _check_python_syntax),
        ("JSON syntax", _check_json),
        ("registry mirrors", _check_registry_mirrors),
        ("Markdown links", _check_markdown_links),
        ("secret scan", _check_secrets),
        ("generated files", _check_generated_files),
    ):
        try:
            ok, detail = function()
        except Exception as exc:  # release gate should report rather than traceback
            ok, detail = False, str(exc)
        report.add(name, ok, detail)

    try:
        validate_model_registry(load_json(ROOT / "config/models.json"))
        validate_source_registry(load_json(ROOT / "config/sources.json"))
        doctor = run_doctor(strict=True)
        report.add("registries and doctor", bool(doctor["healthy"]), doctor["version"])
    except Exception as exc:
        report.add("registries and doctor", False, str(exc))

    with tempfile.TemporaryDirectory(prefix="select-model-release-") as directory:
        temp = Path(directory)
        if not args.skip_tests:
            ok, detail = _run_tests()
            report.add("unit tests", ok, detail)
        if not args.skip_smoke:
            ok, detail = _run_smoke(temp)
            report.add("CLI smoke", ok, detail)
        if not args.skip_install:
            ok, detail = _check_install(temp)
            report.add("isolated install", ok, detail)
        ok, detail = _check_package(temp)
        report.add("skill package", ok, detail)

    ok, detail = _check_generated_files()
    report.add("post-release cleanliness", ok, detail)

    # Remove bytecode that may have been created by an unusual local Python setup.
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for bytecode in ROOT.rglob("*.py[co]"):
        bytecode.unlink(missing_ok=True)

    payload = {"ok": report.ok, "checks": report.checks}
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("release check passed" if report.ok else "release check failed")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
