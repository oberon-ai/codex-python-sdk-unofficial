from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"


class ContributingGuideTests(unittest.TestCase):
    def test_contributing_guide_covers_core_sections(self) -> None:
        content = CONTRIBUTING.read_text(encoding="utf-8")

        expected_sections = [
            "## Ways To Contribute",
            "## Development Environment",
            "## Local Setup",
            "## Daily Workflow",
            "## Examples And Documentation",
            "## Schema Snapshot Workflow",
            "## Protocol Model Generation",
            "## Automated Upstream Tracking",
            "## Dependency Changes",
            "## Design Boundaries",
            "## Pull Request Checklist",
        ]

        for section in expected_sections:
            with self.subTest(section=section):
                self.assertIn(section, content)

    def test_contributing_guide_lists_core_commands(self) -> None:
        content = CONTRIBUTING.read_text(encoding="utf-8")

        expected_commands = [
            "uv sync",
            "uv sync --no-dev",
            "uv sync --group codegen",
            "uv run pytest -q",
            "uv run mypy",
            "uv run ruff check .",
            "uv run ruff format --check .",
            "uv build",
            "uv run python scripts/vendor_protocol_schema.py --check",
            "uv run python scripts/vendor_protocol_schema.py --allow-version-change",
            "uv run --group codegen python scripts/generate_protocol_models.py --check",
            "uv run python -m codex_meta_agent --dry-run",
            (
                "uv run python -m codex_meta_agent --target-version 0.119.0 "
                "--tracking-branch-prefix puck/flegacy-release-- --skip-verification"
            ),
        ]

        for command in expected_commands:
            with self.subTest(command=command):
                self.assertIn(command, content)

    def test_contributing_guide_calls_out_repo_boundaries(self) -> None:
        content = CONTRIBUTING.read_text(encoding="utf-8")

        boundary_markers = [
            "Do not hand-edit files under `src/codex_agent_sdk/generated/`.",
            "transport/",
            "rpc/",
            "protocol/",
            "generated/",
            "codex_agent_sdk",
            "src/codex_meta_agent/",
            "uv.lock",
            "requirements/*.txt",
        ]

        for marker in boundary_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, content)


if __name__ == "__main__":
    unittest.main()
