#!/usr/bin/env python3
"""Build a deterministic, repository-complete ``skill.zip`` using stdlib only."""
from __future__ import annotations

import argparse
import os
import stat
import zipfile
from pathlib import Path

MAX_SKILL_BYTES = 25 * 1024 * 1024
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
}
EXCLUDED_NAMES = {
    ".DS_Store",
    ".coverage",
    "history.jsonl",
    "evidence.jsonl",
    "route-result.json",
    "api-response.json",
    "skill.zip",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp"}
REQUIRED = {"SKILL.md", "agents/openai.yaml", "README.md", "LICENSE"}
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def _include(path: Path, root: Path, output: Path) -> bool:
    if path == output:
        return False
    relative = path.relative_to(root)
    if any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in relative.parts):
        return False
    # Keep reviewed fixtures under examples/, while excluding similarly named
    # runtime outputs and state files everywhere else.
    if path.name in EXCLUDED_NAMES and (not relative.parts or relative.parts[0] != "examples"):
        return False
    if path.suffix in EXCLUDED_SUFFIXES:
        return False
    if path.is_symlink():
        raise ValueError(f"refusing to package symbolic link: {relative}")
    return path.is_file()


def _validate_root(root: Path) -> None:
    missing = sorted(item for item in REQUIRED if not (root / item).is_file())
    if missing:
        raise ValueError("missing required files: " + ", ".join(missing))
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    if not skill.startswith("---\n") or "\nname: select-model\n" not in skill:
        raise ValueError("SKILL.md frontmatter is missing or has an unexpected name")


def build(root: Path, output: Path) -> dict[str, object]:
    root = root.resolve()
    output = output.resolve()
    _validate_root(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (path for path in root.rglob("*") if _include(path, root, output)),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    if not files:
        raise ValueError("no files selected for packaging")

    prefix = root.name
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in files:
            relative = source.relative_to(root).as_posix()
            info = zipfile.ZipInfo(f"{prefix}/{relative}", FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if os.access(source, os.X_OK) else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, source.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    size = output.stat().st_size
    if size > MAX_SKILL_BYTES:
        output.unlink(missing_ok=True)
        raise ValueError(
            f"package is {size} bytes and exceeds the {MAX_SKILL_BYTES}-byte Skill limit"
        )
    return {"output": str(output), "files": len(files), "bytes": size, "root": prefix}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", default="dist/skill.zip")
    args = parser.parse_args()
    try:
        result = build(Path(args.root), Path(args.output))
    except (OSError, ValueError) as exc:
        print(f"build error: {exc}")
        return 2
    print(
        f"built {result['output']} with {result['files']} files "
        f"({result['bytes']} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
