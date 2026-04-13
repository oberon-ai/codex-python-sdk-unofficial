from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PYPROJECT_DATA = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class ProjectConfigurationTests(unittest.TestCase):
    def test_build_backend_and_python_requirement_are_declared(self) -> None:
        build_system = PYPROJECT_DATA["build-system"]
        project = PYPROJECT_DATA["project"]

        self.assertEqual(build_system["build-backend"], "setuptools.build_meta")
        self.assertIn("setuptools>=69", build_system["requires"])
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["name"], "codex-agent-sdk-unofficial")

    def test_dev_extra_includes_quality_tools(self) -> None:
        dev_dependencies = set(PYPROJECT_DATA["project"]["optional-dependencies"]["dev"])

        self.assertTrue(any(dep.startswith("build>=") for dep in dev_dependencies))
        self.assertTrue(any(dep.startswith("mypy>=") for dep in dev_dependencies))
        self.assertTrue(any(dep.startswith("pytest>=") for dep in dev_dependencies))
        self.assertTrue(any(dep.startswith("ruff>=") for dep in dev_dependencies))

    def test_quality_tools_preserve_generated_code_boundary(self) -> None:
        ruff = PYPROJECT_DATA["tool"]["ruff"]
        mypy = PYPROJECT_DATA["tool"]["mypy"]

        self.assertIn("src/codex_agent_sdk/generated", ruff["extend-exclude"])
        self.assertEqual(mypy["exclude"], "^src/codex_agent_sdk/generated/")

    def test_pytest_and_type_marker_exist(self) -> None:
        pytest_config = PYPROJECT_DATA["tool"]["pytest"]["ini_options"]
        py_typed = REPO_ROOT / "src" / "codex_agent_sdk" / "py.typed"

        self.assertEqual(pytest_config["testpaths"], ["tests"])
        self.assertTrue(py_typed.exists())


if __name__ == "__main__":
    unittest.main()
