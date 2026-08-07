from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CliIntegrationTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "scripts/select_model.py", *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_version(self) -> None:
        result = self.run_cli("--version")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("4.0.0", result.stdout)

    def test_route_and_dispatch_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            route_path = Path(directory) / "route.json"
            routed = self.run_cli(
                "route",
                "--input",
                "examples/route-input.json",
                "--history",
                str(Path(directory) / "history.jsonl"),
                "--output",
                str(route_path),
            )
            self.assertEqual(routed.returncode, 0, routed.stdout)
            dispatched = self.run_cli(
                "dispatch",
                "--route",
                str(route_path),
                "--context",
                "examples/context.json",
                "--dry-run",
            )
            self.assertEqual(dispatched.returncode, 0, dispatched.stdout)
            self.assertIn('"dry_run": true', dispatched.stdout)


if __name__ == "__main__":
    unittest.main()
