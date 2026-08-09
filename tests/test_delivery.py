import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = REPO / "scripts" / "verify_delivery.py"


def load_validator():
    if not VALIDATOR_PATH.is_file():
        raise AssertionError("missing delivery validator: scripts/verify_delivery.py")
    spec = importlib.util.spec_from_file_location("verify_delivery", VALIDATOR_PATH)
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
        path.write_text(path.read_text(encoding="utf-8").replace("1.2.0", "1.2.1"), encoding="utf-8")
        self.assertTrue(any("README.md does not identify version" in e for e in self.errors()))

    def test_conflicting_version_declaration_is_reported(self):
        path = self.repo / "README.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n当前版本：1.2.1\n", encoding="utf-8")
        self.assertTrue(any("conflicting version" in e for e in self.errors()))

    def test_tag_version_mismatch_is_reported(self):
        self.assertTrue(any("tag must be v1.2.0" in e for e in self.errors(mode="tag", ref_name="v1.2.1")))

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
        path.write_text(path.read_text(encoding="utf-8").replace("Skill 版本：1.2.0", "Skill 版本：1.2.1"), encoding="utf-8")
        self.assertTrue(any("Issue template does not identify version" in e for e in self.errors()))


if __name__ == "__main__":
    unittest.main()
