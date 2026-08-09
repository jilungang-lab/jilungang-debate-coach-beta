#!/usr/bin/env python3
"""Build and verify the standalone Xiaohongshu SkillHub package."""

from __future__ import annotations

import argparse
from pathlib import Path
import stat
import sys
import zipfile


PACKAGE_ROOT = "jilungang-debate-coach"
MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_PACKAGE_SIZE = 30 * 1024 * 1024
FIXED_TIMESTAMP = (2020, 1, 1, 0, 0, 0)

PACKAGE_FILES = {
    "SKILL.md": "jilungang-debate-coach/SKILL.md",
    "agents/openai.yaml": "jilungang-debate-coach/agents/openai.yaml",
    "evals/regression.json": "jilungang-debate-coach/evals/regression.json",
    "references/method-model.md": "jilungang-debate-coach/references/method-model.md",
    "references/provenance-audit.md": "jilungang-debate-coach/references/provenance-audit.md",
    "references/research-sourcing.md": "jilungang-debate-coach/references/research-sourcing.md",
    "LICENSE": "LICENSE",
    "NOTICE.md": "NOTICE.md",
    "ACKNOWLEDGEMENTS.md": "ACKNOWLEDGEMENTS.md",
    "SUPPORT.md": "SUPPORT.md",
    "VERSION": "VERSION",
}


def _archive_name(relative: str) -> str:
    return f"{PACKAGE_ROOT}/{relative}"


def _validated_source(repo: Path, relative: str) -> tuple[Path, int]:
    source = repo / relative
    current = repo
    try:
        for part in Path(relative).parts:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                raise ValueError(f"symbolic link is not allowed as package source: {relative}")
    except OSError as exc:
        raise ValueError(f"cannot inspect package source {relative}: {exc}") from exc

    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError(f"missing regular package source file: {relative}")
    size = source.stat().st_size
    if size > MAX_FILE_SIZE:
        raise ValueError(f"package source exceeds SkillHub 10 MB limit: {relative}")
    return source, size


def build_package(repo: Path, destination: Path) -> Path:
    repo = Path(repo).resolve()
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)

    sources: dict[str, Path] = {}
    total_size = 0
    for packaged_relative, source_relative in sorted(PACKAGE_FILES.items()):
        source, size = _validated_source(repo, source_relative)
        sources[packaged_relative] = source
        total_size += size
    if total_size > MAX_PACKAGE_SIZE:
        raise ValueError("package sources exceed SkillHub 30 MB limit")

    with zipfile.ZipFile(destination, "w") as archive:
        for packaged_relative, source_relative in sorted(PACKAGE_FILES.items()):
            source = sources[packaged_relative]
            payload = source.read_bytes()
            info = zipfile.ZipInfo(_archive_name(packaged_relative), FIXED_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, payload)

    errors = verify_package(repo, destination)
    if errors:
        destination.unlink(missing_ok=True)
        raise ValueError("invalid Xiaohongshu package: " + "; ".join(errors))
    return destination


def verify_package(repo: Path, package: Path) -> list[str]:
    repo = Path(repo).resolve()
    package = Path(package).resolve()
    errors: list[str] = []

    if not package.is_file():
        return [f"missing package: {package}"]
    if package.stat().st_size > MAX_PACKAGE_SIZE:
        errors.append("package exceeds SkillHub 30 MB limit")

    expected = {_archive_name(relative) for relative in PACKAGE_FILES}
    try:
        archive = zipfile.ZipFile(package)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"package is not a valid ZIP archive: {exc}"]

    with archive:
        names = archive.namelist()
        actual = set(names)
        for name in sorted(actual):
            if names.count(name) > 1:
                errors.append(f"duplicate packaged entry: {name}")
        for missing in sorted(expected - actual):
            errors.append(f"missing packaged file: {missing}")
        for extra in sorted(actual - expected):
            errors.append(f"extra packaged file: {extra}")

        total_uncompressed = sum(info.file_size for info in archive.infolist())
        if total_uncompressed > MAX_PACKAGE_SIZE:
            errors.append("uncompressed package exceeds SkillHub 30 MB limit")

        for packaged_relative, source_relative in sorted(PACKAGE_FILES.items()):
            name = _archive_name(packaged_relative)
            if name not in actual:
                continue
            try:
                source, _ = _validated_source(repo, source_relative)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            info = archive.getinfo(name)
            if info.file_size > MAX_FILE_SIZE:
                errors.append(f"packaged file exceeds SkillHub 10 MB limit: {name}")
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                errors.append(f"symbolic link is not allowed in package: {name}")
            if archive.read(name) != source.read_bytes():
                errors.append(f"packaged file differs from GitHub source: {name}")

        license_name = _archive_name("LICENSE")
        if license_name in actual:
            license_text = archive.read(license_name).decode("utf-8")
            if "PUBLIC BETA EVALUATION LICENSE" not in license_text:
                errors.append("packaged LICENSE is not the public beta license")
            if "only to users explicitly invited" in license_text:
                errors.append("packaged LICENSE still requires an invitation")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if args.verify_only:
        errors = verify_package(args.root, args.output)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
    else:
        build_package(args.root, args.output)

    print(f"OK: Xiaohongshu package {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
