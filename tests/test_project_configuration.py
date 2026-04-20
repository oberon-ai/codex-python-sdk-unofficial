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

        self.assertEqual(build_system["build-backend"], "uv_build")
        self.assertIn("uv_build>=0.10.10,<0.11.0", build_system["requires"])
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["name"], "codex-python-sdk-unofficial")

    def test_dependency_groups_include_quality_and_codegen_tools(self) -> None:
        dependency_groups = PYPROJECT_DATA["dependency-groups"]
        dev_dependencies = set(dependency_groups["dev"])
        codegen_dependencies = set(dependency_groups["codegen"])

        self.assertIn("mypy>=1.20,<2", dev_dependencies)
        self.assertIn("pytest>=9,<10", dev_dependencies)
        self.assertIn("pytest-asyncio>=1.3,<2", dev_dependencies)
        self.assertIn("ruff>=0.15,<0.16", dev_dependencies)
        self.assertEqual(codegen_dependencies, {"datamodel-code-generator>=0.56,<0.57"})

    def test_uv_defaults_to_the_dev_group(self) -> None:
        tool_uv = PYPROJECT_DATA["tool"]["uv"]

        self.assertEqual(tool_uv["default-groups"], ["dev"])
        self.assertEqual(
            tool_uv["build-backend"]["module-name"],
            ["codex_agent_sdk", "codex_meta_agent"],
        )

    def test_quality_tools_preserve_generated_code_boundary(self) -> None:
        ruff = PYPROJECT_DATA["tool"]["ruff"]
        mypy = PYPROJECT_DATA["tool"]["mypy"]
        isort = ruff["lint"]["isort"]

        self.assertIn("src/codex_agent_sdk/generated", ruff["extend-exclude"])
        self.assertIn("codex_agent_sdk", isort["known-first-party"])
        self.assertIn("codex_meta_agent", isort["known-first-party"])
        self.assertEqual(mypy["exclude"], "^src/codex_agent_sdk/generated/")

    def test_pytest_and_type_marker_exist(self) -> None:
        pytest_config = PYPROJECT_DATA["tool"]["pytest"]["ini_options"]
        py_typed = REPO_ROOT / "src" / "codex_agent_sdk" / "py.typed"

        self.assertEqual(pytest_config["testpaths"], ["tests"])
        self.assertTrue(py_typed.exists())


if __name__ == "__main__":
    unittest.main()
