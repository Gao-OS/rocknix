#!/usr/bin/env python3
"""Qualification helper checks using real SquashFS images."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('gaoos-qualify-ota.py')
SPEC = importlib.util.spec_from_file_location('qualify', SCRIPT)
qualify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualify)


@unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'), 'SquashFS tools are required')
class Qualification(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='gaoos-qualification-test-')
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        root = self.work / 'root'
        (root / 'etc').mkdir(parents=True)
        (root / 'etc/systemd/system').mkdir(parents=True)
        (root / 'usr/bin').mkdir(parents=True)
        (root / 'usr/lib/systemd/system').mkdir(parents=True)
        (root / 'etc/gaoos-version').write_bytes(b'20261120\n')
        (root / 'etc/gaoos-ota-sequence').write_bytes(b'0\n')
        (root / 'etc/os-release').write_bytes(b'OS_NAME="GaoOS"\nOS_VERSION="20261120"\nBUILD_ID="' + b'a' * 40 + b'"\n')
        (root / 'usr/bin/tool').write_bytes(bytes(range(256)) * 200)
        (root / 'usr/lib/systemd/system/rocknix-autostart.service').write_text('[Service]\nType=oneshot\n')
        self.system = self.work / 'base.squashfs'
        self.pseudo = self.work / 'base.pseudo'
        qualify.run('mksquashfs', root, self.system, '-noappend', '-quiet', '-processors', '1',
                    '-all-root', '-comp', 'zstd', '-b', '262144',
                    '-p', 'usr/bin/link s 777 0 0 tool',
                    '-p', 'usr/bin/tool x user.gaoos=qualifier')
        qualify.run('unsquashfs', '-no-progress', '-pf', self.pseudo, self.system)
        self.info = {
            'board': 'powkiddy,rgb30', 'arch': 'aarch64', 'boot_layout': 'gaoos-rk3566-ab-v1',
            'sequence': 0, 'version': '20261120', 'git_sha': 'a' * 40, 'kernel_sha256': 'b' * 64,
            'dtb_sha256': 'c' * 64, 'sha256': qualify.image_validation.sha256(self.system),
            'size_bytes': self.system.stat().st_size
        }
        self.source = {'image_sha256': 'd' * 64, 'system_sha256': self.info['sha256'], 'git_sha': self.info['git_sha']}

    def candidate(self, sequence, number, size=1536 * 1024 * 1024, *, fail_boot=False):
        return qualify.qualification_candidate(self.system, self.pseudo, self.work, self.info, self.source,
                                               size, sequence, number, fail_boot=fail_boot)

    def test_real_repack_changes_only_identity_files_and_keeps_exact_boot_identity(self):
        candidates = [self.candidate(sequence, number) for sequence, number in [(3, 1), (4, 2)]]
        self.assertNotEqual(candidates[0]['ota']['sha256'], candidates[1]['ota']['sha256'])
        for number, report in enumerate(candidates, 1):
            artifact = self.work / report['artifact']
            self.assertEqual(report['classification'], 'TEST_ONLY')
            self.assertEqual(report['hardware'], 'HARDWARE_BOOT_UNVERIFIED')
            for key in ('git_sha', 'kernel_sha256', 'dtb_sha256'):
                self.assertEqual(report['ota'][key], self.info[key])
            self.assertEqual(report['ota']['sequence'], number + 2)
            self.assertEqual(report['ota']['version'], f'20261120.qa{number}')
            self.assertEqual(qualify.run('unsquashfs', '-cat', artifact, 'usr/bin/tool'),
                             qualify.run('unsquashfs', '-cat', self.system, 'usr/bin/tool'))
            for flag in ('-lln', '-s'):
                before = qualify.run('unsquashfs', flag, self.system)
                after = qualify.run('unsquashfs', flag, artifact)
                lines = [line for line in before.splitlines() if b'usr/bin/' in line or
                         b'Block size ' in line or b'Compression ' in line]
                self.assertTrue(lines)
                for line in lines:
                    self.assertIn(line, after)
            marker = json.loads(qualify.run('unsquashfs', '-cat', artifact, 'etc/gaoos-ota-qualification'))
            self.assertEqual(marker['source'], self.source)
            candidate_pseudo = self.work / 'candidate.pseudo'
            qualify.run('unsquashfs', '-no-progress', '-pf', candidate_pseudo, artifact)
            xattrs = [line for line in self.pseudo.read_bytes().splitlines() if line.startswith(b'usr/bin/tool x ')]
            self.assertTrue(xattrs)
            for xattr in xattrs:
                self.assertIn(xattr, candidate_pseudo.read_bytes().splitlines())
            candidate_pseudo.unlink()

    def test_over_capacity_artifact_cannot_produce_a_report(self):
        with self.assertRaisesRegex(ValueError, 'slot capacity'):
            self.candidate(3, 1, size=96)
        self.assertEqual(list(self.work.glob('*.json')), [])

    def test_failed_boot_masks_required_unit_in_the_trial_slot_only(self):
        healthy = self.work / self.candidate(3, 1)['artifact']
        failed = self.candidate(5, 3, fail_boot=True)
        failure = self.work / failed['artifact']
        self.assertEqual(failed['ota']['version'], '20261120.qa-fail')
        self.assertEqual(failed['trial'], 'health-check-failure')
        self.assertEqual(failed['classification'], 'TEST_ONLY')
        marker = json.loads(qualify.run('unsquashfs', '-cat', failure, 'etc/gaoos-ota-qualification'))
        self.assertEqual(marker['trial'], failed['trial'])
        for field in ('kernel_sha256', 'dtb_sha256'):
            self.assertEqual(failed['ota'][field], self.info[field])
        target = qualify.FAIL_BOOT_UNIT.decode()
        self.assertIn(b'/' + qualify.FAIL_BOOT_UNIT + b' -> /dev/null',
                      qualify.run('unsquashfs', '-lln', failure, target))
        healthy_files = qualify.run('unsquashfs', '-lln', healthy)
        self.assertNotIn(b'/' + qualify.FAIL_BOOT_UNIT, healthy_files)
        service = 'usr/lib/systemd/system/rocknix-autostart.service'
        self.assertEqual(qualify.run('unsquashfs', '-cat', failure, service),
                         qualify.run('unsquashfs', '-cat', healthy, service))

    def test_signer_refuses_qualification_without_explicit_test_mode(self):
        candidate = self.candidate(3, 3, fail_boot=True)
        artifact = self.work / candidate['artifact']
        cmd = ['python3', SCRIPT.parents[1] / '.github/scripts/sign-ota-release.py',
               '--report', artifact.with_suffix('.json'), '--artifact', artifact, '--commit',
               self.info['git_sha'], '--sequence', '3', '--channel', 'nightly', '--issued-at',
               '1700000000', '--expires-at', '1700003600', '--output', self.work / 'signed.json']
        result = subprocess.run(cmd, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Factory image validation did not pass', result.stderr)

        if not shutil.which('openssl'):
            self.skipTest('OpenSSL is required for explicit qualification signing')
        key = subprocess.check_output(['openssl', 'genpkey', '-algorithm', 'Ed25519'], text=True)
        env = {**os.environ, 'GAOOS_OTA_SIGNING_KEY': key}
        result = subprocess.run([*cmd, '--allow-qualification', '--channel', 'stable'], env=env,
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run([*cmd, '--allow-qualification'], env=env, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / 'signed.json').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
