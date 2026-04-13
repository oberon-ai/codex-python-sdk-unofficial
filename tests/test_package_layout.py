from __future__ import annotations

import importlib
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PackageLayoutTests(unittest.TestCase):
    def test_expected_modules_are_importable(self) -> None:
        modules = [
            "codex_agent_sdk.client",
            "codex_agent_sdk.query",
            "codex_agent_sdk.events",
            "codex_agent_sdk.approvals",
            "codex_agent_sdk.results",
            "codex_agent_sdk.transport",
            "codex_agent_sdk.transport.stdio",
            "codex_agent_sdk.rpc",
            "codex_agent_sdk.rpc.jsonrpc",
            "codex_agent_sdk.rpc.connection",
            "codex_agent_sdk.rpc.router",
            "codex_agent_sdk.generated",
            "codex_agent_sdk.protocol",
            "codex_agent_sdk.protocol.registries",
            "codex_agent_sdk.protocol.adapters",
            "codex_agent_sdk.testing",
            "codex_agent_sdk.testing.fake_app_server",
        ]

        for module_name in modules:
            with self.subTest(module_name=module_name):
                module = importlib.import_module(module_name)
                self.assertEqual(module.__name__, module_name)

    def test_generated_code_location_is_marked_as_machine_written(self) -> None:
        generated_readme = REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "README.md"

        self.assertTrue(generated_readme.exists())
        content = generated_readme.read_text()
        self.assertIn("Do not hand-edit", content)
        self.assertIn("generated", content.lower())

    def test_repository_support_directories_have_placeholders(self) -> None:
        expected_files = [
            REPO_ROOT / "docs" / "package-layout.md",
            REPO_ROOT / "examples" / "README.md",
            REPO_ROOT / "scripts" / "README.md",
        ]

        for path in expected_files:
            with self.subTest(path=path):
                self.assertTrue(path.exists(), str(path))

    def test_layout_doc_calls_out_layer_boundaries(self) -> None:
        content = (REPO_ROOT / "docs" / "package-layout.md").read_text()

        self.assertIn("transport", content)
        self.assertIn("rpc", content)
        self.assertIn("generated", content)
        self.assertIn("public SDK surface", content)
        self.assertIn("testing", content)


if __name__ == "__main__":
    unittest.main()
