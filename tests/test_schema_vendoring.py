from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = REPO_ROOT / "tests" / "fixtures" / "schema_snapshots"
MANIFEST_PATH = SCHEMA_ROOT / "vendor_manifest.json"


def assert_sorted_object_keys(test_case: unittest.TestCase, value: Any) -> None:
    if isinstance(value, dict):
        keys = list(value.keys())
        test_case.assertEqual(keys, sorted(keys))
        for nested in value.values():
            assert_sorted_object_keys(test_case, nested)
        return
    if isinstance(value, list):
        for nested in value:
            assert_sorted_object_keys(test_case, nested)


class SchemaVendoringTests(unittest.TestCase):
    maxDiff = None

    def test_manifest_records_schema_pin_and_artifacts(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema_family"], "codex_app_server_protocol.v2")
        self.assertEqual(manifest["generator"]["codex_cli_version"], "codex-cli 0.118.0")
        self.assertEqual(
            manifest["upstream_reference"]["repository"],
            "openai/codex",
        )
        self.assertEqual(
            manifest["upstream_reference"]["source_path"],
            "codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json",
        )
        self.assertIn("--experimental", manifest["generator"]["command_templates"]["experimental"])
        self.assertNotIn("--experimental", manifest["generator"]["command_templates"]["stable"])
        self.assertTrue(manifest["version_policy"]["update_requires_explicit_opt_in"])
        self.assertEqual(manifest["version_policy"]["update_flag"], "--allow-version-change")

    def test_manifest_hashes_match_vendored_snapshots(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        for name, artifact in manifest["artifacts"].items():
            with self.subTest(artifact=name):
                path = REPO_ROOT / artifact["path"]
                payload = path.read_text(encoding="utf-8")
                sha256 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                parsed = json.loads(payload)

                self.assertEqual(sha256, artifact["sha256"])
                self.assertEqual(len(parsed["definitions"]), artifact["definition_count"])

    def test_schema_snapshots_are_canonicalized_for_reviewable_diffs(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        self.assertEqual(
            manifest["canonicalization"],
            {
                "encoding": "utf-8",
                "indent": 2,
                "json_sort_keys": True,
                "trailing_newline": True,
            },
        )

        for artifact in manifest["artifacts"].values():
            with self.subTest(path=artifact["path"]):
                path = REPO_ROOT / artifact["path"]
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.endswith("\n"))
                parsed = json.loads(text)
                assert_sorted_object_keys(self, parsed)

    def test_experimental_snapshot_is_a_superset_of_stable_snapshot(self) -> None:
        stable = json.loads(
            (SCHEMA_ROOT / "stable" / "codex_app_server_protocol.v2.stable.schemas.json").read_text(
                encoding="utf-8"
            )
        )
        experimental = json.loads(
            (
                SCHEMA_ROOT
                / "experimental"
                / "codex_app_server_protocol.v2.experimental.schemas.json"
            ).read_text(encoding="utf-8")
        )

        stable_definitions = set(stable["definitions"])
        experimental_definitions = set(experimental["definitions"])

        self.assertTrue(stable_definitions.issubset(experimental_definitions))
        self.assertGreater(len(experimental_definitions), len(stable_definitions))


if __name__ == "__main__":
    unittest.main()
