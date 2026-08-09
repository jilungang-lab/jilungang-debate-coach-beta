import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest
import warnings
import zipfile


REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO / "scripts" / "verify_delivery.py"
XHS_BUILDER_PATH = REPO / "scripts" / "build_xhs_package.py"


def load_validator():
    if not VALIDATOR_PATH.is_file():
        raise AssertionError("missing delivery validator: scripts/verify_delivery.py")
    spec = importlib.util.spec_from_file_location("verify_delivery", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_xhs_builder():
    if not XHS_BUILDER_PATH.is_file():
        raise AssertionError("missing Xiaohongshu package builder: scripts/build_xhs_package.py")
    spec = importlib.util.spec_from_file_location("build_xhs_package", XHS_BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DeliveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        shutil.copytree(REPO, self.repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))

    def tearDown(self):
        self.tmp.cleanup()

    def errors(self, *, mode="normal", ref_name=None):
        return self.validator.validate_repo(self.repo, mode=mode, ref_name=ref_name)

    def test_current_repository_satisfies_delivery_contract(self):
        self.assertEqual([], self.validator.validate_repo(REPO))

    def test_missing_skill_file_is_reported(self):
        target = self.repo / "jilungang-debate-coach" / "SKILL.md"
        target.unlink()
        self.assertTrue(any("missing skill file" in e for e in self.errors()))

    def test_extra_skill_file_is_reported(self):
        (self.repo / "jilungang-debate-coach" / "extra.md").write_text("x", encoding="utf-8")
        self.assertTrue(any("extra skill file" in e for e in self.errors()))

    def test_extra_empty_skill_directory_is_reported(self):
        (self.repo / "jilungang-debate-coach" / "old").mkdir()
        self.assertTrue(any("extra skill directory" in e for e in self.errors()))

    def test_modified_skill_file_is_reported(self):
        target = self.repo / "jilungang-debate-coach" / "SKILL.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nchange\n", encoding="utf-8")
        self.assertTrue(any("SHA-256 mismatch" in e for e in self.errors()))

    def test_symbolic_link_is_rejected(self):
        target = self.repo / "jilungang-debate-coach" / "SKILL.md"
        backup = self.repo / "skill-copy.md"
        shutil.copyfile(target, backup)
        target.unlink()
        os.symlink(backup, target)
        self.assertTrue(any("symbolic link" in e for e in self.errors()))

    def test_symbolic_link_skill_root_is_rejected(self):
        target = self.repo / "jilungang-debate-coach"
        backup = self.repo / "skill-backup"
        target.rename(backup)
        os.symlink(backup, target)
        self.assertTrue(any("symbolic link" in e for e in self.errors()))

    def test_non_utf8_skill_file_is_reported(self):
        target = self.repo / "jilungang-debate-coach" / "SKILL.md"
        target.write_bytes(b"\xff\xfe")
        self.assertTrue(any("UTF-8" in e for e in self.errors()))

    def test_bad_json_is_reported(self):
        target = self.repo / "jilungang-debate-coach" / "evals" / "regression.json"
        target.write_text("{", encoding="utf-8")
        self.assertTrue(any("valid JSON" in e for e in self.errors()))

    def test_bad_json_field_type_is_reported(self):
        target = self.repo / "jilungang-debate-coach" / "evals" / "regression.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["cases"] = "fifteen"
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(any("cases must be a list" in e for e in self.errors()))

    def test_non_semver_version_is_reported(self):
        (self.repo / "VERSION").write_text("version one\n", encoding="utf-8")
        self.assertTrue(any("semantic version" in e for e in self.errors()))

    def test_readme_version_mismatch_is_reported(self):
        path = self.repo / "README.md"
        path.write_text(path.read_text(encoding="utf-8").replace("1.2.2", "1.2.3"), encoding="utf-8")
        self.assertTrue(any("README.md does not identify version" in e for e in self.errors()))

    def test_conflicting_version_declaration_is_reported(self):
        path = self.repo / "README.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n当前版本：1.2.3\n", encoding="utf-8")
        self.assertTrue(any("conflicting version" in e for e in self.errors()))

    def test_tag_version_mismatch_is_reported(self):
        self.assertTrue(any("tag must be v1.2.2" in e for e in self.errors(mode="tag", ref_name="v1.2.3")))

    def test_contact_number_is_canonical(self):
        support = (self.repo / "SUPPORT.md").read_text(encoding="utf-8")
        account = re.search(r"小红书号[：:]\s*`?(\d{6,20})`?", support).group(1)
        path = self.repo / "README.md"
        path.write_text(path.read_text(encoding="utf-8") + f"\n{account}\n", encoding="utf-8")
        self.assertTrue(any("only appear in SUPPORT.md" in e for e in self.errors()))

    def test_issue_template_front_matter_is_required(self):
        path = self.repo / ".github" / "ISSUE_TEMPLATE" / "skill-feedback.md"
        path.write_text(path.read_text(encoding="utf-8").replace("---\n", "", 1), encoding="utf-8")
        self.assertTrue(any("front matter delimiters" in e for e in self.errors()))

    def test_issue_template_version_must_match(self):
        path = self.repo / ".github" / "ISSUE_TEMPLATE" / "skill-feedback.md"
        path.write_text(path.read_text(encoding="utf-8").replace("Skill 版本：1.2.2", "Skill 版本：1.2.3"), encoding="utf-8")
        self.assertTrue(any("Issue template does not identify version" in e for e in self.errors()))

    def test_main_push_guard_skips_the_zero_before_sha_on_initial_push(self):
        workflow = (self.repo / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")
        self.assertIn(
            "github.event.before != '0000000000000000000000000000000000000000'",
            workflow,
        )

    def test_ci_builds_the_xiaohongshu_distribution(self):
        workflow = (self.repo / ".github" / "workflows" / "verify.yml").read_text(encoding="utf-8")

        self.assertIn("build_xhs_package.py", workflow)
        self.assertIn("jilungang-debate-coach-xiaohongshu.zip", workflow)

    def test_public_beta_docs_do_not_require_an_invitation(self):
        readme = (self.repo / "README.md").read_text(encoding="utf-8")
        support = (self.repo / "SUPPORT.md").read_text(encoding="utf-8")
        feedback = (self.repo / "FEEDBACK.md").read_text(encoding="utf-8")
        issue_template = (
            self.repo / ".github" / "ISSUE_TEMPLATE" / "skill-feedback.md"
        ).read_text(encoding="utf-8")

        self.assertIn("公开测试", readme)
        self.assertIn("任何人都可查看", support)
        for text in (readme, support, feedback, issue_template):
            self.assertNotIn("私有仓库", text)
            self.assertNotIn("受邀成员", text)

    def test_readme_defines_the_dual_distribution_boundary(self):
        readme = (self.repo / "README.md").read_text(encoding="utf-8")

        self.assertIn("GitHub Release 是版本基准", readme)
        self.assertIn("小红书 Skill Hub", readme)
        self.assertIn("五个方法与评测文件", readme)
        self.assertIn("openai.yaml", readme)
        self.assertIn("VERSION", readme)

    def test_public_beta_license_allows_uninvited_personal_evaluation(self):
        license_text = (self.repo / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("any person", license_text)
        self.assertIn("personal, non-commercial evaluation", license_text)
        self.assertNotIn("only to users explicitly invited", license_text)

    def test_xiaohongshu_package_matches_github_release_files(self):
        builder = load_xhs_builder()
        destination = Path(self.tmp.name) / "xiaohongshu.zip"
        second_destination = Path(self.tmp.name) / "xiaohongshu-second.zip"

        builder.build_package(REPO, destination)
        builder.build_package(REPO, second_destination)

        self.assertEqual([], builder.verify_package(REPO, destination))
        self.assertEqual(destination.read_bytes(), second_destination.read_bytes())
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
        self.assertIn("jilungang-debate-coach/SKILL.md", names)
        self.assertIn("jilungang-debate-coach/LICENSE.txt", names)
        self.assertIn("jilungang-debate-coach/NOTICE.md", names)
        self.assertIn("jilungang-debate-coach/ACKNOWLEDGEMENTS.md", names)
        self.assertIn("jilungang-debate-coach/SUPPORT.md", names)
        self.assertIn("jilungang-debate-coach/VERSION.txt", names)
        self.assertNotIn("jilungang-debate-coach/agents/openai.yaml", names)

    def test_xiaohongshu_package_verifier_reports_a_corrupt_archive(self):
        builder = load_xhs_builder()
        destination = Path(self.tmp.name) / "corrupt.zip"
        destination.write_bytes(b"not a zip archive")

        errors = builder.verify_package(REPO, destination)

        self.assertTrue(any("valid ZIP" in error for error in errors))

    def test_xiaohongshu_builder_rejects_a_source_symlink(self):
        builder = load_xhs_builder()
        source = self.repo / "jilungang-debate-coach" / "SKILL.md"
        backup = self.repo / "skill-copy.md"
        shutil.copyfile(source, backup)
        source.unlink()
        os.symlink(backup, source)

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            builder.build_package(self.repo, Path(self.tmp.name) / "symlink.zip")

    def test_xiaohongshu_builder_rejects_a_symlinked_source_directory(self):
        builder = load_xhs_builder()
        source = self.repo / "jilungang-debate-coach" / "references"
        backup = self.repo / "references-copy"
        shutil.copytree(source, backup)
        shutil.rmtree(source)
        os.symlink(backup, source)

        with self.assertRaisesRegex(ValueError, "symbolic link"):
            builder.build_package(self.repo, Path(self.tmp.name) / "symlink-directory.zip")

    def test_xiaohongshu_builder_rejects_an_oversized_source_file(self):
        builder = load_xhs_builder()
        source = self.repo / "ACKNOWLEDGEMENTS.md"
        source.write_bytes(b"x" * (builder.MAX_FILE_SIZE + 1))

        with self.assertRaisesRegex(ValueError, "10 MB"):
            builder.build_package(self.repo, Path(self.tmp.name) / "oversized.zip")

    def test_xiaohongshu_verifier_rejects_duplicate_entries(self):
        builder = load_xhs_builder()
        destination = Path(self.tmp.name) / "duplicate.zip"
        builder.build_package(REPO, destination)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(destination, "a") as archive:
                archive.writestr(
                    "jilungang-debate-coach/SKILL.md",
                    (REPO / "jilungang-debate-coach" / "SKILL.md").read_bytes(),
                )

        errors = builder.verify_package(REPO, destination)

        self.assertTrue(any("duplicate packaged entry" in error for error in errors))

    def test_xiaohongshu_verifier_rejects_tampered_content(self):
        builder = load_xhs_builder()
        original = Path(self.tmp.name) / "original.zip"
        tampered = Path(self.tmp.name) / "tampered.zip"
        builder.build_package(REPO, original)
        with zipfile.ZipFile(original) as source_archive, zipfile.ZipFile(tampered, "w") as target_archive:
            for info in source_archive.infolist():
                payload = source_archive.read(info.filename)
                if info.filename == "jilungang-debate-coach/SKILL.md":
                    payload += b"\ntampered\n"
                target_archive.writestr(info, payload)

        errors = builder.verify_package(REPO, tampered)

        self.assertTrue(any("differs from GitHub source" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
