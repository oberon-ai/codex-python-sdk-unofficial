from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYPROJECT_DATA = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class DependencyPolicyTests(unittest.TestCase):
    def test_runtime_dependency_is_single_pydantic_entry(self) -> None:
        dependencies = PYPROJECT_DATA["project"]["dependencies"]

        self.assertEqual(dependencies, ["pydantic>=2.13,<3"])

    def test_optional_dependency_groups_keep_codegen_separate(self) -> None:
        optional_dependencies = PYPROJECT_DATA["project"]["optional-dependencies"]
        dev_dependencies = optional_dependencies["dev"]
        codegen_dependencies = optional_dependencies["codegen"]

        self.assertIn("pytest-asyncio>=1.3,<2", dev_dependencies)
        self.assertEqual(codegen_dependencies, ["datamodel-code-generator>=0.56,<0.57"])

    def test_pinned_requirement_files_exist(self) -> None:
        runtime_requirements = (REPO_ROOT / "requirements" / "runtime.txt").read_text(
            encoding="utf-8"
        )
        dev_requirements = (REPO_ROOT / "requirements" / "dev.txt").read_text(encoding="utf-8")
        codegen_requirements = (REPO_ROOT / "requirements" / "codegen.txt").read_text(
            encoding="utf-8"
        )

        self.assertIn("pydantic==2.13.0", runtime_requirements)
        self.assertIn("-r runtime.txt", dev_requirements)
        self.assertIn("pytest-asyncio==1.3.0", dev_requirements)
        self.assertIn("-r runtime.txt", codegen_requirements)
        self.assertIn("datamodel-code-generator==0.56.0", codegen_requirements)

    def test_dependency_policy_doc_records_asyncio_only_rationale(self) -> None:
        policy_doc = REPO_ROOT / "docs" / "dependency-policy.md"
        content = policy_doc.read_text(encoding="utf-8")

        self.assertTrue(policy_doc.exists())
        self.assertIn("asyncio`-only", content)
        self.assertIn("pydantic", content)
        self.assertIn("pytest-asyncio", content)
        self.assertIn("datamodel-code-generator", content)
        self.assertIn("There is no docs-specific dependency group yet.", content)


if __name__ == "__main__":
    unittest.main()
