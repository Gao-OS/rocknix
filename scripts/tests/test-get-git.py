#!/usr/bin/env python3
"""Exercise Git cache validation with real repositories and advertised refs."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


GET_GIT = Path(__file__).resolve().parents[1] / "get_git"


class GitCacheValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="get-git-test-", dir="/tmp")
        cls.repo = Path(cls.temp.name) / "repo"
        cls.repo.mkdir()
        cls.env = os.environ.copy()
        cls.env.update(
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL="/dev/null",
            GIT_AUTHOR_NAME="Git validator test",
            GIT_AUTHOR_EMAIL="validator@example.invalid",
            GIT_COMMITTER_NAME="Git validator test",
            GIT_COMMITTER_EMAIL="validator@example.invalid",
        )
        cls.git("init", "-q", "--initial-branch=main")
        (cls.repo / "README").write_text("fixture\n")
        cls.git("add", "README")
        cls.git("commit", "-q", "-m", "fixture")
        cls.pin = cls.git("rev-parse", "HEAD")
        cls.url = "https://example.invalid/fixture.git"
        cls.git("remote", "add", "origin", cls.url)
        cls.git(
            "update-ref", "--stdin",
            input="".join(
                f"update refs/tags/ref-{i:05d} {cls.pin}\n" for i in range(16384)
            ),
        )
        cls.git("pack-refs", "--all")
        cls.unadvertised = cls.git("commit-tree", cls.git("write-tree"), input="other\n")
        source = GET_GIT.read_text()
        assert source.endswith("\nmain\n")
        cls.validator = source[:-len("\nmain\n")] + "\n_repo_exists_and_valid\n"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def git(cls, *args, input=None):
        return subprocess.check_output(
            ["git", *args], cwd=cls.repo, env=cls.env, text=True, input=input,
            stderr=subprocess.PIPE,
        ).strip()

    def validate(self, **overrides):
        env = self.env.copy()
        env.update(
            PACKAGE=str(self.repo), PKG_VERSION=self.pin, PKG_URL=self.url,
            PKG_GIT_CLONE_BRANCH="", _debug="",
        )
        env.update(overrides)
        return subprocess.run(
            ["bash", "-c", self.validator], cwd=self.temp.name, env=env,
            text=True, capture_output=True, timeout=30,
        )

    def test_accepts_correct_pin_with_many_refs(self):
        refs = self.git("ls-remote", ".")
        self.assertEqual(refs.splitlines()[0], f"{self.pin}\tHEAD")
        self.assertGreater(len(refs), 512 * 1024)
        result = self.validate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_abbreviated_pin(self):
        result = self.validate(PKG_VERSION=self.pin[:12])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_accepts_requested_branch(self):
        result = self.validate(PKG_GIT_CLONE_BRANCH="main")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_wrong_pin(self):
        self.assertEqual(self.validate(PKG_VERSION="0" * 40).returncode, 1)

    def test_rejects_unadvertised_commit(self):
        self.assertEqual(self.validate(PKG_VERSION=self.unadvertised).returncode, 1)

    def test_rejects_wrong_origin(self):
        self.assertEqual(self.validate(PKG_URL=self.url + "/other").returncode, 1)

    def test_rejects_wrong_branch(self):
        self.assertEqual(self.validate(PKG_GIT_CLONE_BRANCH="other").returncode, 1)

    def test_rejects_missing_repository(self):
        self.assertEqual(self.validate(PACKAGE=str(self.repo / "missing")).returncode, 1)

    def test_rejects_invalid_repository(self):
        invalid = Path(self.temp.name) / "invalid"
        invalid.mkdir()
        self.assertEqual(self.validate(PACKAGE=str(invalid)).returncode, 1)


if __name__ == "__main__":
    unittest.main()
