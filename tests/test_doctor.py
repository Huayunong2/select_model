from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from select_model.doctor import run_doctor
from tests.helpers import model_registry, source_registry


class DoctorTests(unittest.TestCase):
    def test_strict_mode_enforces_registry_freshness(self) -> None:
        models = model_registry()
        for model in models["models"].values():
            model["pricing"]["observed_at"] = "2000-01-01T00:00:00Z"
        sources = source_registry()

        with tempfile.TemporaryDirectory() as directory:
            model_path = Path(directory) / "models.json"
            source_path = Path(directory) / "sources.json"
            model_path.write_text(json.dumps(models), encoding="utf-8")
            source_path.write_text(json.dumps(sources), encoding="utf-8")

            advisory = run_doctor(
                model_registry_path=str(model_path),
                source_registry_path=str(source_path),
                strict=False,
            )
            strict = run_doctor(
                model_registry_path=str(model_path),
                source_registry_path=str(source_path),
                strict=True,
            )

        self.assertTrue(advisory["healthy"])
        self.assertFalse(strict["healthy"])
        pricing = next(item for item in strict["checks"] if item["name"] == "pricing_freshness")
        self.assertFalse(pricing["ok"])


if __name__ == "__main__":
    unittest.main()
