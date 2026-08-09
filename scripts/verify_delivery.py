#!/usr/bin/env python3
"""Verify the reproducible-delivery contract for the public beta repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


EXPECTED_VERSION = "1.2.3"
SKILL_DIR = "jilungang-debate-coach"
SKILL_HASHES = {
    "SKILL.md": "ca06e340afa950effde55dac45c561a88edd452922d3020be349b19de99b76ce",
    "agents/openai.yaml": "cd29bec94b50a145bdcfa0b0ffc6c9402502e8981671d950a361e43aa27d145d",
    "evals/regression.json": "592ff58c9302394582e854943dc0346ae5e586ef8358c8d774342d7fb731f4be",
    "references/method-model.md": "4821f8d1016e9cb2a0c848e81889df1b7bdd8d709f5bad0070dc918f85684f9f",
    "references/provenance-audit.md": "d030aea6d607de22dbaa892de17a72212653235e6560573fc33c90d3e99223d6",
    "references/research-sourcing.md": "328e0039b44feb7d910042357e75cd3a833b64bda181ffeb20f7b69e0b6d751e",
}
REQUIRED_ROOT_FILES = {
    ".github/ISSUE_TEMPLATE/skill-feedback.md",
    ".github/workflows/verify.yml",
    "ACKNOWLEDGEMENTS.md",
    "FEEDBACK.md",
    "LICENSE",
    "NOTICE.md",
    "README.md",
    "SUPPORT.md",
    "VERSION",
    "scripts/verify_delivery.py",
    "scripts/build_xhs_package.py",
    "tests/test_delivery.py",
}
LOCAL_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")
SEMVER = re.compile(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\Z")


def _read_utf8(path: Path, errors: list[str], label: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{label} is not valid UTF-8")
    except OSError as exc:
        errors.append(f"cannot read {label}: {exc}")
    return None


def _validate_skill(root: Path, errors: list[str]) -> None:
    skill = root / SKILL_DIR
    expected = set(SKILL_HASHES)
    try:
        skill_mode = skill.lstat().st_mode
    except OSError:
        errors.append(f"missing required directory: {SKILL_DIR}")
        return
    if stat.S_ISLNK(skill_mode):
        errors.append(f"symbolic link is not allowed: {SKILL_DIR}")
        return
    if not stat.S_ISDIR(skill_mode):
        errors.append(f"required path is not a directory: {SKILL_DIR}")
        return

    actual: set[str] = set()
    actual_directories: set[str] = set()
    for directory, dirnames, filenames in os.walk(skill, followlinks=False):
        base = Path(directory)
        for dirname in list(dirnames):
            path = base / dirname
            if path.is_symlink():
                actual.add(path.relative_to(skill).as_posix() + "/")
                errors.append(f"symbolic link is not allowed: {path.relative_to(root).as_posix()}")
                dirnames.remove(dirname)
            else:
                actual_directories.add(path.relative_to(skill).as_posix())
        for filename in filenames:
            actual.add((base / filename).relative_to(skill).as_posix())

    allowed_directories = {"agents", "evals", "references"}
    for extra in sorted(actual_directories - allowed_directories):
        errors.append(f"extra skill directory: {SKILL_DIR}/{extra}")

    for missing in sorted(expected - actual):
        errors.append(f"missing skill file: {SKILL_DIR}/{missing}")
    for extra in sorted(actual - expected):
        errors.append(f"extra skill file: {SKILL_DIR}/{extra}")

    readable: dict[str, str] = {}
    for relative in sorted(expected & actual):
        path = skill / relative
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            errors.append(f"cannot inspect skill file {relative}: {exc}")
            continue
        if stat.S_ISLNK(mode):
            errors.append(f"symbolic link is not allowed: {SKILL_DIR}/{relative}")
            continue
        if not stat.S_ISREG(mode):
            errors.append(f"skill path is not a regular file: {SKILL_DIR}/{relative}")
            continue
        try:
            payload = path.read_bytes()
        except OSError as exc:
            errors.append(f"cannot read skill file {relative}: {exc}")
            continue
        try:
            readable[relative] = payload.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"skill file {relative} is not valid UTF-8")
        digest = hashlib.sha256(payload).hexdigest()
        if digest != SKILL_HASHES[relative]:
            errors.append(f"SHA-256 mismatch: {SKILL_DIR}/{relative}")

    skill_md = readable.get("SKILL.md", "")
    if "name: jilungang-debate-coach" not in skill_md:
        errors.append("SKILL.md does not declare the expected skill name")
    if "description:" not in skill_md:
        errors.append("SKILL.md does not declare a trigger description")

    regression = readable.get("evals/regression.json")
    if regression is None:
        return
    try:
        data = json.loads(regression)
    except json.JSONDecodeError:
        errors.append("evals/regression.json is not valid JSON")
        return
    if not isinstance(data, dict):
        errors.append("regression JSON top level must be an object")
        return
    if data.get("skill_name") != SKILL_DIR:
        errors.append(f"regression skill_name must be {SKILL_DIR}")
    if not isinstance(data.get("schema_version"), str):
        errors.append("regression schema_version must be a string")
    cases = data.get("cases")
    if not isinstance(cases, list):
        errors.append("regression cases must be a list")
        return
    if len(cases) < 15:
        errors.append("regression cases must contain at least 15 entries")
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"regression case {index} must be an object")
            continue
        for key in ("name", "task", "prompt", "input_context"):
            if not isinstance(case.get(key), str):
                errors.append(f"regression case {index} field {key} must be a string")
        for key in ("checks", "quality_notes"):
            value = case.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                errors.append(f"regression case {index} field {key} must be a list of strings")


def _validate_local_links(root: Path, markdown: Path, text: str, errors: list[str]) -> None:
    for raw_target in LOCAL_LINK.findall(text):
        target = raw_target.split("#", 1)[0].strip()
        if not target or target.startswith(("http://", "https://", "mailto:", "/")):
            continue
        path = (markdown.parent / target).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{markdown.name} link escapes repository: {raw_target}")
            continue
        if not path.exists():
            errors.append(f"{markdown.name} has missing local link: {raw_target}")


def validate_repo(root: Path, *, mode: str = "normal", ref_name: str | None = None) -> list[str]:
    root = Path(root)
    errors: list[str] = []

    for relative in sorted(REQUIRED_ROOT_FILES):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing required file: {relative}")

    _validate_skill(root, errors)

    version_path = root / "VERSION"
    version = _read_utf8(version_path, errors, "VERSION").strip() if version_path.is_file() else ""
    if not SEMVER.fullmatch(version):
        errors.append(f"VERSION must be a semantic version, found {version or '<empty>'}")
    if version and version != EXPECTED_VERSION:
        errors.append(f"VERSION must be {EXPECTED_VERSION}, found {version}")

    readable: dict[str, str] = {}
    for relative in ("README.md", "FEEDBACK.md", "LICENSE", "NOTICE.md", "SUPPORT.md"):
        path = root / relative
        if path.is_file():
            text = _read_utf8(path, errors, relative)
            if text is not None:
                readable[relative] = text
                _validate_local_links(root, path, text, errors)

    version_markers = {
        "README.md": f"这是 `jilungang-debate-coach` {version} 的公开测试版。",
        "FEEDBACK.md": f"当前 Skill 版本：`jilungang-debate-coach` {version}。",
        "SUPPORT.md": f"当前公开测试版本：`jilungang-debate-coach` {version}。",
    }
    for relative, marker in version_markers.items():
        text = readable.get(relative, "")
        if text and version and marker not in text:
            errors.append(f"{relative} does not identify version {version}")
        declared = set(re.findall(r"(?<!\d)\d+\.\d+\.\d+(?!\d)", text))
        if version and declared - {version}:
            errors.append(f"{relative} contains a conflicting version declaration")

    support = readable.get("SUPPORT.md", "")
    for phrase in (
        "任何人都可查看",
        "Issue 不是一对一私信",
        "小红书",
        "无法判断",
        "立即停止追加",
        "不保证能够彻底清除",
        "确认收件",
        "已接受但暂不处理",
        "私信中的敏感内容不会搬回 Issue",
    ):
        if support and phrase not in support:
            errors.append(f"SUPPORT.md is missing routing rule: {phrase}")

    for relative in ("README.md", "FEEDBACK.md", "NOTICE.md"):
        text = readable.get(relative, "")
        if text and "SUPPORT.md" not in text:
            errors.append(f"{relative} does not link to SUPPORT.md")

    readme = readable.get("README.md", "")
    for phrase in ("GitHub Release 是版本基准", "小红书 Skill Hub", "五个方法与评测文件", "openai.yaml"):
        if readme and phrase not in readme:
            errors.append(f"README.md is missing dual distribution rule: {phrase}")

    license_text = readable.get("LICENSE", "")
    for phrase in (
        "PUBLIC BETA EVALUATION LICENSE",
        "any person",
        "personal, non-commercial evaluation",
    ):
        if license_text and phrase not in license_text:
            errors.append(f"LICENSE is missing public evaluation grant: {phrase}")
    if "only to users explicitly invited" in license_text:
        errors.append("LICENSE still restricts evaluation to invited users")

    account_matches = re.findall(r"小红书号[：:]\s*`?(\d{6,20})`?", support)
    if len(account_matches) != 1:
        errors.append("SUPPORT.md must contain exactly one complete Xiaohongshu account number")
    elif any(account_matches[0] in text for name, text in readable.items() if name != "SUPPORT.md"):
        errors.append("the complete contact number must only appear in SUPPORT.md")

    template_path = root / ".github/ISSUE_TEMPLATE/skill-feedback.md"
    if template_path.is_file():
        template = _read_utf8(template_path, errors, "Issue template") or ""
        front_matter: dict[str, str] = {}
        if not template.startswith("---\n") or "\n---\n" not in template[4:]:
            errors.append("Issue template must begin with valid front matter delimiters")
        else:
            header = template[4:].split("\n---\n", 1)[0]
            for line in header.splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    front_matter[key.strip()] = value.strip()
            for field in ("name", "about", "title", "labels", "assignees"):
                if field not in front_matter:
                    errors.append(f"Issue template front matter is missing: {field}")
        privacy_at = template.find("## 提交前先检查")
        input_at = template.find("## 最小复现")
        if privacy_at < 0 or input_at < 0 or privacy_at > input_at:
            errors.append("Issue template must put privacy checks before input fields")
        for phrase in ("不要创建 Issue", "脱敏", "不是一对一私信", "/blob/main/SUPPORT.md"):
            if phrase not in template:
                errors.append(f"Issue template is missing privacy rule: {phrase}")
        expected_template_version = f"- Skill 版本：{version}"
        if version and expected_template_version not in template:
            errors.append(f"Issue template does not identify version {version}")
        declared = set(re.findall(r"(?<!\d)\d+\.\d+\.\d+(?!\d)", template))
        if version and declared - {version}:
            errors.append("Issue template contains a conflicting version declaration")

    workflow_path = root / ".github/workflows/verify.yml"
    if workflow_path.is_file():
        workflow = _read_utf8(workflow_path, errors, "verification workflow") or ""
        for phrase in ("pull_request:", "workflow_dispatch:", "tags:", "fetch-depth: 0", "actions/setup-python", "python-version: '3.12'", "unittest discover", "verify_delivery.py", "build_xhs_package.py"):
            if phrase not in workflow:
                errors.append(f"verification workflow is missing: {phrase}")

    if mode == "tag":
        expected_tag = f"v{version}" if version else "v<missing>"
        if ref_name != expected_tag:
            errors.append(f"tag must be {expected_tag}, found {ref_name or '<missing>'}")
    elif mode != "normal":
        errors.append(f"unknown validation mode: {mode}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--mode", choices=("normal", "tag"), default="normal")
    parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME"))
    args = parser.parse_args()
    errors = validate_repo(args.root, mode=args.mode, ref_name=args.ref_name)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"OK: beta delivery contract {EXPECTED_VERSION}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
