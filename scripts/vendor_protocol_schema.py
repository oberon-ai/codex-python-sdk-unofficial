#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SNAPSHOTS_ROOT = REPO_ROOT / "tests" / "fixtures" / "schema_snapshots"
MANIFEST_PATH = SCHEMA_SNAPSHOTS_ROOT / "vendor_manifest.json"
SCHEMA_FAMILY = "codex_app_server_protocol.v2"
UPSTREAM_REPOSITORY = "openai/codex"
UPSTREAM_SOURCE_PATH = (
    "codex-rs/app-server-protocol/schema/json/codex_app_server_protocol.v2.schemas.json"
)
CANONICALIZATION_DESCRIPTION = {
    "json_sort_keys": True,
    "indent": 2,
    "trailing_newline": True,
    "encoding": "utf-8",
}


@dataclass(frozen=True)
class SchemaSnapshotMode:
    name: str
    target_path: Path
    experimental: bool
    command_template: str


SNAPSHOT_MODES = (
    SchemaSnapshotMode(
        name="stable",
        target_path=SCHEMA_SNAPSHOTS_ROOT
        / "stable"
        / "codex_app_server_protocol.v2.stable.schemas.json",
        experimental=False,
        command_template="codex app-server generate-json-schema --out <OUT_DIR>",
    ),
    SchemaSnapshotMode(
        name="experimental",
        target_path=SCHEMA_SNAPSHOTS_ROOT
        / "experimental"
        / "codex_app_server_protocol.v2.experimental.schemas.json",
        experimental=True,
        command_template=("codex app-server generate-json-schema --experimental --out <OUT_DIR>"),
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Refresh or verify the vendored Codex app-server JSON Schema snapshots.")
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Path to the codex binary used to generate the schema snapshots.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify the current vendored artifacts instead of rewriting them.",
    )
    parser.add_argument(
        "--allow-version-change",
        action="store_true",
        help=(
            "Allow the manifest's pinned codex-cli version to change when "
            "refreshing the vendored snapshots."
        ),
    )
    return parser


def run_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )


def read_codex_version(codex_bin: str) -> str:
    completed = run_command([codex_bin, "--version"])
    version = completed.stdout.strip()
    if not version:
        raise SystemExit(f"`{codex_bin} --version` produced no output.")
    return version


def load_existing_manifest() -> dict[str, Any] | None:
    if not MANIFEST_PATH.exists():
        return None
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def canonicalize_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_schema_snapshot(codex_bin: str, mode: SchemaSnapshotMode) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"codex-schema-{mode.name}-") as tmpdir:
        cmd = [codex_bin, "app-server", "generate-json-schema"]
        if mode.experimental:
            cmd.append("--experimental")
        cmd.extend(["--out", tmpdir])
        run_command(cmd)
        bundle_path = Path(tmpdir) / "codex_app_server_protocol.v2.schemas.json"
        return json.loads(bundle_path.read_text(encoding="utf-8"))


def ensure_version_is_expected(
    current_version: str,
    allow_version_change: bool,
) -> None:
    manifest = load_existing_manifest()
    if manifest is None:
        return
    expected_version = manifest["generator"]["codex_cli_version"]
    if current_version == expected_version or allow_version_change:
        return
    raise SystemExit(
        "Refusing to refresh schema snapshots with a different codex-cli version.\n"
        f"Manifest pin: {expected_version}\n"
        f"Current binary: {current_version}\n"
        "Install the pinned version or rerun with --allow-version-change for an "
        "intentional pin bump."
    )


def build_manifest(
    *,
    codex_version: str,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_family": SCHEMA_FAMILY,
        "source_of_truth": (
            "tests/fixtures/schema_snapshots/{stable,experimental}/"
            "codex_app_server_protocol.v2.*.schemas.json"
        ),
        "version_policy": {
            "pin_type": "codex_cli_version",
            "current_pin": codex_version,
            "update_requires_explicit_opt_in": True,
            "update_flag": "--allow-version-change",
        },
        "upstream_reference": {
            "repository": UPSTREAM_REPOSITORY,
            "source_path": UPSTREAM_SOURCE_PATH,
        },
        "generator": {
            "codex_cli_version": codex_version,
            "command_templates": {mode.name: mode.command_template for mode in SNAPSHOT_MODES},
        },
        "canonicalization": CANONICALIZATION_DESCRIPTION,
        "artifacts": artifacts,
    }


def render_artifacts(codex_bin: str) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    rendered: dict[str, str] = {}
    artifacts: dict[str, dict[str, Any]] = {}
    for mode in SNAPSHOT_MODES:
        payload = generate_schema_snapshot(codex_bin=codex_bin, mode=mode)
        canonical_text = canonicalize_json(payload)
        rendered[mode.name] = canonical_text
        artifacts[mode.name] = {
            "path": str(mode.target_path.relative_to(REPO_ROOT)),
            "sha256": sha256_text(canonical_text),
            "definition_count": len(payload.get("definitions", {})),
            "experimental": mode.experimental,
        }
    return rendered, artifacts


def write_outputs(rendered: dict[str, str], manifest: dict[str, Any]) -> None:
    for mode in SNAPSHOT_MODES:
        mode.target_path.write_text(rendered[mode.name], encoding="utf-8")
    MANIFEST_PATH.write_text(canonicalize_json(manifest), encoding="utf-8")


def verify_outputs(rendered: dict[str, str], manifest: dict[str, Any]) -> None:
    mismatches: list[str] = []
    for mode in SNAPSHOT_MODES:
        current_path = mode.target_path
        if not current_path.exists():
            mismatches.append(f"Missing snapshot: {current_path}")
            continue
        current_text = current_path.read_text(encoding="utf-8")
        if current_text != rendered[mode.name]:
            mismatches.append(f"Snapshot drift: {current_path} does not match regenerated output.")
    current_manifest = load_existing_manifest()
    if current_manifest != manifest:
        mismatches.append(f"Manifest drift: {MANIFEST_PATH} does not match regenerated metadata.")
    if mismatches:
        raise SystemExit("\n".join(mismatches))


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    codex_version = read_codex_version(args.codex_bin)
    ensure_version_is_expected(
        current_version=codex_version,
        allow_version_change=args.allow_version_change,
    )
    rendered, artifacts = render_artifacts(args.codex_bin)
    manifest = build_manifest(codex_version=codex_version, artifacts=artifacts)

    if args.check:
        verify_outputs(rendered, manifest)
        print(
            "Vendored schema snapshots match the pinned "
            f"{manifest['generator']['codex_cli_version']} generator."
        )
        return 0

    write_outputs(rendered, manifest)
    print(f"Wrote vendored schema snapshots for {manifest['generator']['codex_cli_version']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
