from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PUBLIC_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "README.md",
    REPO_ROOT / "docs" / "api.md",
    REPO_ROOT / "docs" / "codex-options.md",
    REPO_ROOT / "docs" / "dependency-policy.md",
    REPO_ROOT / "docs" / "package-layout.md",
    REPO_ROOT / "docs" / "protocol-model-codegen.md",
    REPO_ROOT / "docs" / "public-import-policy.md",
    REPO_ROOT / "docs" / "schema-vendoring.md",
    REPO_ROOT / "examples" / "README.md",
    REPO_ROOT / "scripts" / "README.md",
    REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "README.md",
]

RETIRED_DOCS = [
    REPO_ROOT / "docs" / "public-api-contract.md",
    REPO_ROOT / "docs" / "ergonomics-mapping.md",
    REPO_ROOT / "docs" / "upstream-reference-map.md",
    REPO_ROOT / "docs" / "adr" / "0001-native-async-app-server-scope.md",
    REPO_ROOT / "docs" / "adr" / "0002-concurrency-and-state-model.md",
    REPO_ROOT / "docs" / "adr" / "0003-errors-timeouts-and-cancellation.md",
]

BANNED_TERMS = (
    "YoloPilot",
    "Puck Code",
    "Oberon",
    ".yolopilot",
    "workstream",
    "iteration session",
    "task manifest",
)


class DocumentationCleanupTests(unittest.TestCase):
    def test_curated_public_docs_exist(self) -> None:
        for path in PUBLIC_DOCS:
            with self.subTest(path=path):
                self.assertTrue(path.exists(), str(path))

    def test_retired_planning_docs_are_absent(self) -> None:
        for path in RETIRED_DOCS:
            with self.subTest(path=path):
                self.assertFalse(path.exists(), str(path))

    def test_public_docs_do_not_leak_internal_workflow_terms(self) -> None:
        for path in PUBLIC_DOCS:
            content = path.read_text(encoding="utf-8")
            for term in BANNED_TERMS:
                with self.subTest(path=path, term=term):
                    self.assertNotIn(term, content)

    def test_readme_describes_current_preview_surface(self) -> None:
        content = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        lowered = content.lower()

        self.assertIn("status: preview", lowered)
        self.assertIn("query()", content)
        self.assertIn("AppServerClient", content)
        self.assertIn("CodexSDKClient", content)
        self.assertIn("SyncCodexSDKClient", content)
        self.assertIn("private event loop thread", lowered)
        self.assertIn("less natural", lowered)


if __name__ == "__main__":
    unittest.main()
