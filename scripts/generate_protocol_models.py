#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
VENDOR_MANIFEST_PATH = (
    REPO_ROOT / "tests" / "fixtures" / "schema_snapshots" / "vendor_manifest.json"
)
CODEGEN_REQUIREMENTS_PATH = REPO_ROOT / "requirements" / "codegen.txt"
OUTPUT_PATH = REPO_ROOT / "src" / "codex_agent_sdk" / "generated" / "stable.py"

SCHEMA_ARTIFACT_NAME = "stable"
GENERATOR_MODULE = "datamodel_code_generator"
SCHEMA_VERSION = "draft-07"
CODEGEN_FLAGS = (
    "--input-file-type",
    "jsonschema",
    "--schema-version",
    SCHEMA_VERSION,
    "--output-model-type",
    "pydantic_v2.BaseModel",
    "--target-python-version",
    "3.11",
    "--use-standard-collections",
    "--use-union-operator",
    "--enum-field-as-literal",
    "one",
    "--field-constraints",
    "--use-subclass-enum",
    "--use-annotated",
    "--snake-case-field",
    "--allow-population-by-field-name",
    "--base-class",
    "codex_agent_sdk.protocol.pydantic.WireModel",
    "--disable-timestamp",
    "--no-allow-remote-refs",
    "--formatters",
    "ruff-format",
    "ruff-check",
)


@dataclass(frozen=True)
class GenerationTarget:
    schema_artifact_name: str
    schema_path: Path
    schema_sha256: str
    output_path: Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or verify the stable Pydantic wire models from the pinned "
            "vendored Codex app-server schema snapshot."
        )
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the checked-in generated models match regenerated output.",
    )
    return parser


def load_vendor_manifest() -> dict[str, Any]:
    return json.loads(VENDOR_MANIFEST_PATH.read_text(encoding="utf-8"))


def load_generation_target() -> GenerationTarget:
    manifest = load_vendor_manifest()
    artifact = manifest["artifacts"][SCHEMA_ARTIFACT_NAME]
    return GenerationTarget(
        schema_artifact_name=SCHEMA_ARTIFACT_NAME,
        schema_path=REPO_ROOT / artifact["path"],
        schema_sha256=artifact["sha256"],
        output_path=OUTPUT_PATH,
    )


def read_codegen_version_pin() -> str:
    for line in CODEGEN_REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("datamodel-code-generator=="):
            return stripped.removeprefix("datamodel-code-generator==")
    raise SystemExit(
        f"Could not find a datamodel-code-generator pin in {CODEGEN_REQUIREMENTS_PATH}."
    )


def read_installed_codegen_version() -> str:
    completed = subprocess.run(
        [sys.executable, "-m", GENERATOR_MODULE, "--version"],
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    version_text = completed.stdout.strip()
    if not version_text:
        raise SystemExit(f"`{sys.executable} -m {GENERATOR_MODULE} --version` produced no output.")
    return version_text.split()[-1]


def ensure_codegen_version_matches_pin() -> str:
    pinned_version = read_codegen_version_pin()
    installed_version = read_installed_codegen_version()
    if installed_version != pinned_version:
        raise SystemExit(
            "Installed datamodel-code-generator version does not match the repo pin.\n"
            f"Pinned version: {pinned_version}\n"
            f"Installed version: {installed_version}\n"
            "Install the pinned maintainer toolchain with:\n"
            "python -m pip install -e . -r requirements/dev.txt -r requirements/codegen.txt"
        )
    return installed_version


def build_codegen_command(*, schema_path: Path, output_path: Path) -> list[str]:
    return [
        sys.executable,
        "-m",
        GENERATOR_MODULE,
        "--input",
        str(schema_path),
        "--output",
        str(output_path),
        *CODEGEN_FLAGS,
    ]


def run_codegen(*, schema_path: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="codex-wire-models-") as tmpdir:
        rendered_output_path = Path(tmpdir) / "stable.py"
        completed = subprocess.run(
            build_codegen_command(schema_path=schema_path, output_path=rendered_output_path),
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            raise SystemExit(
                "datamodel-code-generator failed while rendering the stable wire models."
            )
        return postprocess_rendered_output(rendered_output_path.read_text(encoding="utf-8"))


def postprocess_rendered_output(rendered_text: str) -> str:
    """Apply repo-specific conventions that the generator cannot express directly."""

    processed = rendered_text.replace(
        "from codex_agent_sdk.protocol.pydantic import WireModel",
        "from codex_agent_sdk.protocol.pydantic import WireModel, WireRootModel",
    )
    return re.sub(
        r"(\bclass\s+\w+\s*\()\s*RootModel\[",
        r"\1WireRootModel[",
        processed,
        flags=re.MULTILINE,
    )


def write_output(*, target: GenerationTarget, rendered_text: str) -> None:
    target.output_path.write_text(rendered_text, encoding="utf-8")


def verify_output(*, target: GenerationTarget, rendered_text: str) -> None:
    if not target.output_path.exists():
        raise SystemExit(f"Missing generated output: {target.output_path}")
    current_text = target.output_path.read_text(encoding="utf-8")
    if current_text != rendered_text:
        raise SystemExit(
            "Generated wire models are out of date.\n"
            "Run `python scripts/generate_protocol_models.py` to refresh "
            f"{target.output_path.relative_to(REPO_ROOT)}."
        )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    pinned_codegen_version = ensure_codegen_version_matches_pin()
    target = load_generation_target()
    rendered_text = run_codegen(schema_path=target.schema_path)

    if args.check:
        verify_output(target=target, rendered_text=rendered_text)
        print(
            "Stable generated wire models match the pinned schema snapshot and "
            f"datamodel-code-generator {pinned_codegen_version}."
        )
        return 0

    write_output(target=target, rendered_text=rendered_text)
    print(
        "Wrote stable generated wire models to "
        f"{target.output_path.relative_to(REPO_ROOT)} "
        f"from {target.schema_path.relative_to(REPO_ROOT)} "
        f"(schema sha256 {target.schema_sha256})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
