from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_ROOT = REPO_ROOT / "tests" / "fixtures"


class FixtureLayoutTests(unittest.TestCase):
    def test_fixture_tree_contains_expected_categories(self) -> None:
        expected_directories = [
            FIXTURES_ROOT,
            FIXTURES_ROOT / "jsonrpc" / "requests",
            FIXTURES_ROOT / "jsonrpc" / "responses",
            FIXTURES_ROOT / "jsonrpc" / "notifications",
            FIXTURES_ROOT / "jsonrpc" / "server_requests",
            FIXTURES_ROOT / "schema_snapshots" / "stable",
            FIXTURES_ROOT / "schema_snapshots" / "experimental",
            FIXTURES_ROOT / "fake_server_scripts",
            FIXTURES_ROOT / "golden_transcripts" / "turns",
            FIXTURES_ROOT / "golden_transcripts" / "approvals",
        ]

        for path in expected_directories:
            with self.subTest(path=path):
                self.assertTrue(path.is_dir(), str(path))

    def test_fixture_tree_contains_category_readmes(self) -> None:
        expected_readmes = [
            FIXTURES_ROOT / "README.md",
            FIXTURES_ROOT / "jsonrpc" / "requests" / "README.md",
            FIXTURES_ROOT / "jsonrpc" / "responses" / "README.md",
            FIXTURES_ROOT / "jsonrpc" / "notifications" / "README.md",
            FIXTURES_ROOT / "jsonrpc" / "server_requests" / "README.md",
            FIXTURES_ROOT / "schema_snapshots" / "stable" / "README.md",
            FIXTURES_ROOT / "schema_snapshots" / "experimental" / "README.md",
            FIXTURES_ROOT / "fake_server_scripts" / "README.md",
            FIXTURES_ROOT / "golden_transcripts" / "turns" / "README.md",
            FIXTURES_ROOT / "golden_transcripts" / "approvals" / "README.md",
        ]

        for path in expected_readmes:
            with self.subTest(path=path):
                self.assertTrue(path.exists(), str(path))

    def test_fixture_readme_documents_naming_and_boundaries(self) -> None:
        content = (FIXTURES_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn(".request.json", content)
        self.assertIn(".response.json", content)
        self.assertIn(".notification.json", content)
        self.assertIn(".server_request.json", content)
        self.assertIn(".script.jsonl", content)
        self.assertIn(".turn.jsonl", content)
        self.assertIn(".approval.jsonl", content)
        self.assertIn("stable", content)
        self.assertIn("experimental", content)
        self.assertIn("vendor_manifest.json", content)
        self.assertIn("src/codex_agent_sdk/generated/", content)
        self.assertIn("scripts/vendor_protocol_schema.py", content)
        self.assertIn("Integration recordings", content)

    def test_package_layout_doc_mentions_fixtures_tree(self) -> None:
        content = (REPO_ROOT / "docs" / "package-layout.md").read_text(encoding="utf-8")

        self.assertIn("tests/fixtures/", content)
        self.assertIn("schema snapshots", content)
        self.assertIn("golden transcripts", content)


if __name__ == "__main__":
    unittest.main()
