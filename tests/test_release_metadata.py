from __future__ import annotations

from codex_meta_agent.release_metadata import (
    DEFAULT_RELEASE_TAG_PREFIX,
    build_release_tag,
    normalize_release_version,
)


def test_release_metadata_helpers_match_expected_semver_behavior() -> None:
    assert DEFAULT_RELEASE_TAG_PREFIX == "v"
    assert normalize_release_version("rust-v0.119.0") == "0.119.0"
    assert normalize_release_version("0.119.0") == "0.119.0"
    assert build_release_tag("rust-v0.119.0") == "v0.119.0"
