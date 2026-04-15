from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"
PYPROJECT_DATA = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
UV_LOCK_DATA = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))


def locked_version(name: str) -> str:
    versions: set[str] = set()
    for package in UV_LOCK_DATA["package"]:
        version = package.get("version")
        if package.get("name") == name and isinstance(version, str):
            versions.add(version)
    if len(versions) != 1:
        raise AssertionError(
            f"Expected exactly one locked version for {name!r}, found {versions!r}."
        )
    return versions.pop()


class DependencyPolicyTests(unittest.TestCase):
    def test_runtime_dependency_is_single_pydantic_entry(self) -> None:
        dependencies = PYPROJECT_DATA["project"]["dependencies"]

        self.assertEqual(dependencies, ["pydantic>=2.13,<3"])

    def test_dependency_groups_keep_codegen_separate(self) -> None:
        dependency_groups = PYPROJECT_DATA["dependency-groups"]
        dev_dependencies = dependency_groups["dev"]
        codegen_dependencies = dependency_groups["codegen"]

        self.assertIn("pytest-asyncio>=1.3,<2", dev_dependencies)
        self.assertEqual(codegen_dependencies, ["datamodel-code-generator>=0.56,<0.57"])

    def test_uv_lock_records_exact_runtime_and_tooling_versions(self) -> None:
        self.assertEqual(locked_version("pydantic"), "2.13.0")
        self.assertEqual(locked_version("pytest-asyncio"), "1.3.0")
        self.assertEqual(locked_version("datamodel-code-generator"), "0.56.0")

    def test_dependency_policy_doc_records_asyncio_only_rationale(self) -> None:
        policy_doc = REPO_ROOT / "docs" / "dependency-policy.md"
        content = policy_doc.read_text(encoding="utf-8")

        self.assertTrue(policy_doc.exists())
        self.assertIn("asyncio`-only", content)
        self.assertIn("uv.lock", content)
        self.assertIn("uv sync", content)
        self.assertIn("uv build", content)
        self.assertIn("pydantic", content)
        self.assertIn("pytest-asyncio", content)
        self.assertIn("datamodel-code-generator", content)
        self.assertIn("There is no docs-specific dependency group yet.", content)


if __name__ == "__main__":
    unittest.main()
