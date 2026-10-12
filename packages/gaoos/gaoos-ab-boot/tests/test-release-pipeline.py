#!/usr/bin/env python3
"""Release regressions with disposable payloads and inert command stubs."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = ROOT / '.github/workflows'


def job(text, name):
    match = re.search(r'^  ' + re.escape(name) + r':\n', text, re.M)
    if not match:
        raise AssertionError('Missing job ' + name)
    end = re.search(r'^  [\w-]+:\n', text[match.end():], re.M)
    return text[match.end():match.end() + end.start()] if end else text[match.end():]


def run_script(script, args, cwd, env=None):
    return subprocess.run(['bash', str(script), *args], cwd=cwd, env=env,
                          capture_output=True, text=True)


class ReleasePipeline(unittest.TestCase):
    def test_release_jobs_directly_depend_on_release_flags(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        for name in ['release-nightly', 'release-official']:
            block = job(source, name)
            needs = re.search(r'^\s+needs:([^\n]*)(?:\n(?:\s+- [^\n]+))*', block, re.M).group()
            self.assertIn('set-envs', needs)
            self.assertIn('build-devices', needs)

    def test_all_build_stages_keep_distro_and_archive_roots_consistent(self):
        for path in WORKFLOWS.glob('build*.yml'):
            source = path.read_text()
            if './scripts/get_env' not in source:
                continue
            with self.subTest(workflow=path.name):
                self.assertTrue(re.search(r'^      DISTRO: GaoOS$', source, re.M), path.name + ' missing DISTRO=GaoOS')
                self.assertNotIn('build.ROCKNIX-', source)
                self.assertNotIn('target/ROCKNIX-', source)

    def test_ota_artifact_upload_contains_raw_payload(self):
        source = (WORKFLOWS / 'build-aarch64-image.yml').read_text()
        self.assertTrue('target/GaoOS-*.squashfs' in source, 'Raw OTA payload missing from upload')
        self.assertIn('target/GaoOS-*.squashfs.sha256', source)
        self.assertIn('target/GaoOS-*.img.gz', source)

    def test_both_release_jobs_call_shared_raw_manifest_generator(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        for name in ['release-nightly', 'release-official']:
            block = job(source, name)
            self.assertIn('generate-update-manifests.sh', block)
            self.assertNotIn('for img in GaoOS-*.img.gz', block)

    def test_manifests_use_verified_raw_bytes_for_nightly_and_official(self):
        script = ROOT / '.github/scripts/generate-update-manifests.sh'
        self.assertTrue(script.exists(), 'Shared raw manifest generator is missing')
        for tag in ['nightly-20261010', '20261010']:
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                raw = b'hsqs' + bytes(range(128))
                payload = td / 'GaoOS-RK3566.aarch64-20261010.squashfs'
                payload.write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                (td / (payload.name + '.sha256')).write_text(digest + '  ' + payload.name + '\n')
                (td / 'GaoOS-RK3566.aarch64-20261010-Generic.img.gz').write_bytes(b'disk image')
                result = run_script(script, ['20261010', tag], td)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                manifest = json.loads((td / 'manifest-RK3566.json').read_text())
                self.assertEqual(manifest['version'], '20261010')
                self.assertEqual(manifest['device'], 'RK3566')
                self.assertEqual(manifest['url'], 'https://github.com/Gao-OS/rocknix/releases/download/' + tag + '/' + payload.name)
                self.assertEqual(manifest['sha256'], digest)
                self.assertEqual(manifest['size_bytes'], len(raw))

    def test_manifest_keeps_payload_build_date_when_release_crosses_midnight(self):
        script = ROOT / '.github/scripts/generate-update-manifests.sh'
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            payload = td / 'GaoOS-RK3566.aarch64-20261009.squashfs'
            raw = b'hsqs-build-before-midnight'
            payload.write_bytes(raw)
            (td / (payload.name + '.sha256')).write_text(hashlib.sha256(raw).hexdigest() + '  ' + payload.name + '\n')
            result = run_script(script, ['20261010', 'nightly-20261010'], td)
            self.assertEqual(result.returncode, 0, result.stderr)
            manifest = json.loads((td / 'manifest-RK3566.json').read_text())
            self.assertEqual(manifest['version'], '20261009')
            self.assertIn('/nightly-20261010/' + payload.name, manifest['url'])

    def test_manifest_generation_rejects_missing_raw_or_bad_checksum(self):
        script = ROOT / '.github/scripts/generate-update-manifests.sh'
        self.assertTrue(script.exists(), 'Shared raw manifest generator is missing')
        for bad_hash in [False, True]:
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                (td / 'GaoOS-RK3566.aarch64-20261010.img.gz').write_bytes(b'whole disk')
                if bad_hash:
                    payload = td / 'GaoOS-RK3566.aarch64-20261010.squashfs'
                    payload.write_bytes(b'hsqsraw')
                    (td / (payload.name + '.sha256')).write_text('0' * 64 + '  ' + payload.name + '\n')
                result = run_script(script, ['20261010', '20261010'], td)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((td / 'manifest-RK3566.json').exists())

    def test_release_markdown_is_inert_and_dates_still_expand(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / 'GaoOS-RK3566.aarch64-20261010.squashfs').write_bytes(b'hsqsraw')
            commands = td / 'bin'
            commands.mkdir()
            called = td / 'called'
            for name in ['gaoos-update', 'reboot', 'bash']:
                stub = commands / name
                stub.write_text('#!/bin/sh\nprintf "%s\\n" "' + name + ':$*" >> "' + str(called) + '"\n')
                stub.chmod(0o755)
            xml = commands / 'xmlstarlet'
            xml.write_text('#!/bin/sh\ncase \"$*\" in *\"//rocknix/*\"*) echo RK3566 ;; esac\n')
            xml.chmod(0o755)
            env = os.environ.copy()
            env['PATH'] = str(commands) + ':' + env['PATH']
            result = subprocess.run(['/bin/bash', str(ROOT / '.github/scripts/generate-release-body.sh'), '20261010', 'HEAD'],
                                    cwd=td, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = called.read_text() if called.exists() else ''
            self.assertNotIn('gaoos-update:', calls)
            self.assertNotIn('reboot:', calls)
            self.assertNotIn('bash:gaoos-update', calls)
            self.assertNotIn('bash:\n', calls)
            self.assertIn('```bash\ngaoos-update check\ngaoos-update canary\nreboot\ngaoos-update status\n```', result.stdout)
            self.assertIn('/compare/HEAD...20261010', result.stdout)
            self.assertIn('GaoOS-RK3566.aarch64-20261010.squashfs', result.stdout)

    def test_release_tables_link_actual_artifacts_across_midnight(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            payload = 'GaoOS-RK3566.aarch64-20261009.squashfs'
            images = ['GaoOS-RK3566.aarch64-20261009-' + variant + '.img.gz'
                      for variant in ['Generic', 'Specific']]
            for filename in [payload, *images]:
                (td / filename).write_bytes(b'fixture')
            commands = td / 'bin'
            commands.mkdir()
            xml = commands / 'xmlstarlet'
            xml.write_text('''#!/bin/sh
case "$*" in
  *"//rocknix/*"*) printf 'RK3566\\nRK3326\\n' ;;
  *"//rocknix/RK3566/*[@mkimage_options]"*) printf 'Generic\\nSpecific\\n' ;;
  *"//rocknix/RK3566/Generic/file[@full]"*) echo 'Generic handhelds' ;;
  *"//rocknix/RK3566/Specific/file[@full]"*) echo 'Specific handhelds' ;;
esac
''')
            xml.chmod(0o755)
            env = os.environ.copy()
            env['PATH'] = str(commands) + ':' + env['PATH']
            result = subprocess.run(['/bin/bash', str(ROOT / '.github/scripts/generate-release-body.sh'), '20261010', ''],
                                    cwd=td, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for filename in [payload, *images]:
                self.assertIn('[' + filename + '](https://github.com/Gao-OS/rocknix/releases/download/20261010/' + filename + ')', result.stdout)
            self.assertIn('**Generic handhelds**', result.stdout)
            self.assertIn('**Specific handhelds**', result.stdout)
            self.assertNotIn('GaoOS-RK3566.aarch64-20261010', result.stdout)
            self.assertNotIn('GaoOS-RK3326.aarch64-', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
