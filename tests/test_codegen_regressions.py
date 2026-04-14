from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CODEGEN_SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_protocol_models.py"
GENERATED_MODEL_PATH = REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "stable.py"
GENERATED_NOTIFICATION_REGISTRY_PATH = (
    REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "stable_notification_registry.py"
)
GENERATED_SERVER_REQUEST_REGISTRY_PATH = (
    REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "stable_server_request_registry.py"
)
VENDOR_MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "schema_snapshots" / "vendor_manifest.json"
)


def load_codegen_script_module() -> object:
    spec = importlib.util.spec_from_file_location("generate_protocol_models", CODEGEN_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load module spec for {CODEGEN_SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodegenRegressionTests(unittest.TestCase):
    codegen_script: Any
    vendor_manifest: dict[str, Any]
    stable_target: Any
    pinned_codegen_version: str
    stable_generated_text: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.codegen_script = load_codegen_script_module()
        cls.vendor_manifest = json.loads(VENDOR_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.stable_target = cls.codegen_script.load_generation_target()
        cls.pinned_codegen_version = cls.codegen_script.read_codegen_version_pin()
        cls.stable_generated_text = GENERATED_MODEL_PATH.read_text(encoding="utf-8")

    def test_stable_generated_module_header_matches_current_codegen_inputs(self) -> None:
        expected_header = self.codegen_script.build_stable_output_header(
            target=self.stable_target,
            pinned_codegen_version=self.pinned_codegen_version,
        )
        current_header, separator, _ = self.stable_generated_text.partition("\n\n")

        self.assertEqual(separator, "\n\n")
        self.assertEqual(current_header, expected_header)

    def test_generated_registry_files_match_current_renderers(self) -> None:
        expected_notification_registry = self.codegen_script.render_notification_registry(
            rendered_text=self.stable_generated_text,
            target=self.stable_target,
            pinned_codegen_version=self.pinned_codegen_version,
        )
        self.assertEqual(
            GENERATED_NOTIFICATION_REGISTRY_PATH.read_text(encoding="utf-8"),
            expected_notification_registry,
        )

        expected_server_request_registry = self.codegen_script.render_server_request_registry(
            target=self.stable_target,
            pinned_codegen_version=self.pinned_codegen_version,
        )
        self.assertEqual(
            GENERATED_SERVER_REQUEST_REGISTRY_PATH.read_text(encoding="utf-8"),
            expected_server_request_registry,
        )

    def test_codegen_defaults_to_stable_snapshot_while_experimental_snapshot_is_tracked(
        self,
    ) -> None:
        artifacts = self.vendor_manifest["artifacts"]
        self.assertEqual(set(artifacts), {"experimental", "stable"})

        stable_path = REPO_ROOT / artifacts["stable"]["path"]
        experimental_path = REPO_ROOT / artifacts["experimental"]["path"]

        self.assertEqual(self.stable_target.schema_path, stable_path)
        self.assertNotEqual(stable_path, experimental_path)
        self.assertTrue(stable_path.exists())
        self.assertTrue(experimental_path.exists())

        stable_payload = json.loads(stable_path.read_text(encoding="utf-8"))
        experimental_payload = json.loads(experimental_path.read_text(encoding="utf-8"))
        stable_definitions = set(stable_payload["definitions"])
        experimental_definitions = set(experimental_payload["definitions"])

        self.assertTrue(stable_definitions.issubset(experimental_definitions))
        self.assertGreater(len(experimental_definitions), len(stable_definitions))

    @unittest.skipUnless(
        importlib.util.find_spec("datamodel_code_generator") is not None
        and shutil.which("ruff") is not None,
        "requires datamodel-code-generator and ruff in the active environment",
    )
    def test_codegen_check_command_passes_with_maintainer_toolchain(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(CODEGEN_SCRIPT_PATH), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )

        if completed.returncode != 0:
            self.fail(
                "generate_protocol_models.py --check failed.\n"
                f"stdout:\n{completed.stdout}\n"
                f"stderr:\n{completed.stderr}"
            )

        self.assertIn(
            "Stable generated wire models, notification registry, and server request "
            "registry match the pinned schema snapshot",
            completed.stdout,
        )


if __name__ == "__main__":
    unittest.main()
