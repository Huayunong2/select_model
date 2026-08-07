from __future__ import annotations

import unittest

from select_model.dispatch import build_request, dispatch
from select_model.errors import DispatchError, ValidationError
from tests.helpers import model_registry


ROUTE = {
    "handoff": {
        "model": "gpt-5.6-luna",
        "reasoning_effort": "medium",
        "target_runtime": "api",
        "risk": "medium",
        "required_capabilities": [],
    }
}


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = model_registry()

    def test_file_ids_are_not_portable_until_attached(self) -> None:
        _, report = build_request(
            ROUTE,
            {
                "input": "Summarize the file",
                "file_ids": ["file_123"],
                "required_capabilities": ["files"],
            },
            model_registry=self.registry,
        )
        self.assertFalse(report["safe"])
        self.assertIn("files", report["missing"])

    def test_explicit_file_attachment_is_observable(self) -> None:
        payload, report = build_request(
            ROUTE,
            {
                "input": "Summarize the file",
                "file_ids": ["file_123"],
                "attach_file_ids": True,
                "required_capabilities": ["files"],
            },
            model_registry=self.registry,
        )
        self.assertTrue(report["safe"])
        self.assertIn("input_file", str(payload["input"]))

    def test_input_history_is_sent_and_counts_as_conversation(self) -> None:
        payload, report = build_request(
            ROUTE,
            {
                "input_history": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "second"},
                ],
                "input": "third",
                "required_capabilities": ["conversation"],
            },
            model_registry=self.registry,
        )
        self.assertEqual(len(payload["input"]), 3)
        self.assertTrue(report["safe"])

    def test_prompt_override_does_not_claim_history(self) -> None:
        _, report = build_request(
            ROUTE,
            {
                "input_history": [
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "second"},
                ],
                "required_capabilities": ["conversation"],
            },
            prompt_override="replacement",
            model_registry=self.registry,
        )
        self.assertFalse(report["safe"])
        self.assertIn("conversation", report["missing"])

    def test_context_capability_fields_must_be_arrays(self) -> None:
        with self.assertRaises(ValidationError):
            build_request(
                ROUTE,
                {"input": "Fix it", "portable_capabilities": "repo"},
                model_registry=self.registry,
            )

    def test_high_risk_context_loss_requires_separate_force_flag(self) -> None:
        high_risk = {
            "handoff": {
                **ROUTE["handoff"],
                "risk": "high",
                "required_capabilities": ["repo"],
            }
        }
        with self.assertRaises(DispatchError):
            dispatch(
                high_risk,
                {"input": "Fix it"},
                allow_context_loss=True,
                dry_run=True,
                model_registry=self.registry,
            )

    def test_custom_endpoint_refuses_openai_key_variable(self) -> None:
        with self.assertRaisesRegex(DispatchError, "OPENAI_API_KEY"):
            dispatch(
                ROUTE,
                {"input": "Hello"},
                endpoint="https://gateway.example/v1/responses",
                allow_custom_endpoint=True,
                model_registry=self.registry,
            )

    def test_host_state_cannot_be_declared_into_existence(self) -> None:
        _, report = build_request(
            ROUTE,
            {
                "input": "Fix it",
                "required_capabilities": ["repo"],
                "portable_capabilities": ["repo"],
            },
            model_registry=self.registry,
        )
        self.assertFalse(report["safe"])
        self.assertIn("repo", report["host_only_required"])


if __name__ == "__main__":
    unittest.main()
