#!/usr/bin/env python3
"""Run build_distro's release cleanup in isolated filesystem fixtures."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


BUILD_DISTRO = Path(__file__).resolve().parents[1] / "build_distro"
PREFIX = "GaoOS-RK3566.aarch64-"


class ReleasePruneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="build-distro-prune-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for directory in (
            "scripts", "config", "projects/ROCKNIX/devices/RK3566", "bin",
            "release", "target",
        ):
            (self.root / directory).mkdir(parents=True)
        self.executable("scripts/checkdeps", "#!/bin/sh\nexit 0\n")
        self.executable("bin/make", '#!/bin/sh\nprintf "%s\\n" "$@" > make.args\n')
        (self.root / "config/options").write_text("ENABLE_32BIT=false\n")
        (self.root / "projects/ROCKNIX/devices/RK3566/options").write_text("")

    def executable(self, name, source):
        path = self.root / name
        path.write_text(source)
        path.chmod(0o755)

    def build(self):
        env = os.environ.copy()
        env.update(
            DISTRO="GaoOS", PROJECT="ROCKNIX", DEVICE="RK3566",
            DEVICE_ROOT="RK3566", ARCH="aarch64", DIRTY="1",
            PATH=f"{self.root / 'bin'}:{os.environ['PATH']}",
        )
        result = subprocess.run(
            ["bash", str(BUILD_DISTRO)], cwd=self.root, env=env,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.root / "make.args").read_text(), "image\n")

    def preserves_directory(self, location):
        directory = self.root / location / (PREFIX + "retained build")
        (directory / "nested").mkdir(parents=True)
        child = directory / "nested/payload"
        child.write_bytes(b"retained payload\x00\n")
        self.build()
        self.assertTrue(directory.is_dir())
        self.assertEqual(child.read_bytes(), b"retained payload\x00\n")

    def test_preserves_matching_release_directory(self):
        self.preserves_directory("release")

    def test_preserves_matching_target_directory(self):
        self.preserves_directory("target")

    def test_removes_matching_files_and_symlinks_only(self):
        file_target = self.root / "retained file"
        file_target.write_text("keep file\n")
        directory_target = self.root / "retained directory"
        directory_target.mkdir()
        (directory_target / "child").write_text("keep child\n")
        removed = []
        preserved = []
        for location in ("release", "target"):
            directory = self.root / location
            for name in ("20261010.img.gz", "old build\timage.img"):
                artifact = directory / (PREFIX + name)
                artifact.write_text("obsolete\n")
                removed.append(artifact)
            for name, target in (
                ("file-link", file_target),
                ("directory-link", directory_target),
                ("dangling-link", self.root / "absent"),
            ):
                artifact = directory / (PREFIX + name)
                artifact.symlink_to(target)
                removed.append(artifact)
            for name in (
                "ROCKNIX-RK3566.aarch64-old.img",
                "GaoOS-RK3326.aarch64-old.img",
                "GaoOS-RK3566.arm-old.img",
            ):
                artifact = directory / name
                artifact.write_text("keep unrelated\n")
                preserved.append(artifact)
        self.build()
        for artifact in removed:
            self.assertFalse(os.path.lexists(artifact), str(artifact))
        for artifact in preserved:
            self.assertEqual(artifact.read_text(), "keep unrelated\n")
        self.assertEqual(file_target.read_text(), "keep file\n")
        self.assertEqual((directory_target / "child").read_text(), "keep child\n")
        self.assertFalse((self.root / "absent").exists())

    def test_empty_patterns_succeed(self):
        self.build()
        for location in ("release", "target"):
            self.assertEqual(list((self.root / location).iterdir()), [])

    def test_missing_release_and_target_succeed(self):
        for location in ("release", "target"):
            (self.root / location).rmdir()
        self.build()
        for location in ("release", "target"):
            self.assertFalse((self.root / location).exists())


if __name__ == "__main__":
    unittest.main()
