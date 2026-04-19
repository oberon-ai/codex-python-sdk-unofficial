"""Lightweight helpers for stable release naming and normalization."""

from __future__ import annotations

import re

DEFAULT_RELEASE_TAG_PREFIX = "v"


def normalize_release_version(upstream_tag: str) -> str:
    """Extract the semver portion from an upstream release tag."""

    tag = upstream_tag.strip()
    semver_pattern = (
        r"\d+\.\d+\.\d+"
        r"(?:-[0-9A-Za-z][0-9A-Za-z.-]*)?"
        r"(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?"
    )
    for pattern in (
        rf"^(?:.+-)?v(?P<version>{semver_pattern})$",
        rf"^(?P<version>{semver_pattern})$",
    ):
        match = re.fullmatch(pattern, tag)
        if match is not None:
            return match.group("version")
    raise ValueError(f"Upstream release tag {upstream_tag!r} does not contain a semver version.")


def build_release_tag(upstream_tag: str, *, prefix: str = DEFAULT_RELEASE_TAG_PREFIX) -> str:
    """Build this repository's release tag for one upstream release tag."""

    return f"{prefix}{normalize_release_version(upstream_tag)}"


__all__ = [
    "DEFAULT_RELEASE_TAG_PREFIX",
    "build_release_tag",
    "normalize_release_version",
]
