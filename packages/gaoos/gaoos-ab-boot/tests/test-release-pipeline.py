#!/usr/bin/env python3
"""Release regressions with disposable payloads and inert command stubs."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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
    def assert_permission(self, block, resource, access):
        self.assertRegex(block, r'(?m)^\s+permissions:\n(?:\s+[\w-]+: \w+\n)*?\s+'
                         + resource + ': ' + access + r'\s*$')

    def test_nested_build_callers_allow_required_actions_permission(self):
        nightly = (WORKFLOWS / 'build-nightly.yml').read_text()
        device = (WORKFLOWS / 'build-device.yml').read_text()
        self.assert_permission(job(nightly, 'build-devices'), 'actions', 'write')
        self.assert_permission(job(nightly, 'build-devices'), 'contents', 'read')
        for name in ['build-aarch64-toolchain', 'build-aarch64-emu-libretro', 'purge-artifact']:
            self.assert_permission(job(device, name), 'actions', 'write')
            self.assert_permission(job(device, name), 'contents', 'read')

    def test_write_permissions_are_scoped_to_publication_and_retry_jobs(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        defaults = source[:source.index('\njobs:')]
        self.assertIn('\npermissions:\n  contents: read\n', defaults)
        self.assertNotIn(': write', defaults)
        self.assert_permission(job(source, 'build-docker'), 'packages', 'write')
        for name in ['release-nightly', 'release-official']:
            block = job(source, name)
            self.assert_permission(block, 'contents', 'write')
            self.assertIn("github.event_name != 'pull_request'", block)
        retry = job(source, 'rerun-failed-jobs')
        self.assert_permission(retry, 'actions', 'write')
        self.assertIn("github.event_name != 'pull_request'", retry)
        retry_workflow = (WORKFLOWS / 'retry-workflow.yml').read_text()
        self.assert_permission(job(retry_workflow, 'rerun'), 'actions', 'write')
        self.assertNotIn('pull_request_target:', source)
        self.assertNotIn('write-all', source)

    def test_docker_authentication_is_skipped_for_pull_requests(self):
        source = (WORKFLOWS / 'build-docker-image.yml').read_text()
        block = job(source, 'build-docker-image')
        self.assert_permission(block, 'contents', 'read')
        self.assert_permission(block, 'packages', 'write')
        login = block[block.index('- name: Login to GHCR'):block.index('- name: Build and push')]
        self.assertIn("if: github.event_name != 'pull_request'", login)
        self.assertIn("push: ${{ github.event_name != 'pull_request' }}", block)

    def test_private_build_image_read_permission_through_reusable_workflows(self):
        nightly = (WORKFLOWS / 'build-nightly.yml').read_text()
        device = (WORKFLOWS / 'build-device.yml').read_text()
        self.assert_permission(job(nightly, 'build-devices'), 'packages', 'read')
        self.assertIn('\npermissions:\n  contents: read\n  packages: read\n', device)
        for name in ['build-aarch64-toolchain', 'build-aarch64-emu-libretro']:
            self.assert_permission(job(device, name), 'packages', 'read')
            leaf = (WORKFLOWS / f'{name}.yml').read_text()
            self.assert_permission(job(leaf, name), 'packages', 'read')
        for path in WORKFLOWS.glob('build-*.yml'):
            content = path.read_text()
            if not ('docker run' in content and '/rocknix-build:latest' in content):
                continue
            with self.subTest(workflow=path.name):
                login = content.index('uses: docker/login-action@v4')
                self.assertLess(login, content.index('docker run'))
                self.assertIn("if: github.event_name != 'pull_request'", content[:login])

    def test_signed_ota_requires_validated_same_run_artifacts_and_protected_signing(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        block = job(source, 'register-ota')
        image = (WORKFLOWS / 'build-aarch64-image.yml').read_text()
        self.assertIn("github.event_name == 'workflow_dispatch'", block)
        self.assertIn("github.repository == 'Gao-OS/rocknix'", block)
        self.assertIn("github.ref == 'refs/heads/gaoos'", block)
        self.assertIn("- validate-only", source)
        self.assertNotIn("validate-only", block)
        self.assertIn("OTA_CHANNEL:", job(source, "build-devices"))
        self.assertIn('environment: ota-signing', block)
        self.assertNotIn('GAOOS_OTA_SIGNING_KEY', image)
        self.assertIn('gaoos-validate-image.py', image)
        self.assertIn('GaoOS-ota-RGB30-validation', image)
        self.assertIn('GaoOS-ota-RGB30-validation', block)
        self.assertLess(block.index('sign-ota-release.py'), block.index('register-ota-release.sh'))
        self.assertNotIn('contents: write', block)
        for name in ['release-nightly', 'release-official']:
            self.assertIn("inputs.ota_channel == 'disabled'", job(source, name))

    def test_stable_release_remains_draft_without_hardware_acceptance(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        block = job(source, 'release-official')
        self.assertRegex(block, r'(?m)^\s+draft: true$')

    def test_nightly_stays_draft_without_hardware_acceptance(self):
        source = (WORKFLOWS / 'build-nightly.yml').read_text()
        block = job(source, 'release-nightly')
        release = block[block.index('uses: ncipollo/release-action@v1'):]
        self.assertRegex(release, r'(?m)^\s+prerelease: true$')
        self.assertRegex(release, r'(?m)^\s+draft: true$')
        self.assertIn("commit: ${{ github.sha }}", release)
        self.assertIn("allowUpdates: false", release)
        self.assertIn("skipIfReleaseExists: true", release)
        self.assertNotIn("git tag -f", block)
        self.assertNotIn("git push", block)
        body = release[release.index('body:'):release.index('token:')]
        self.assertIn('HARDWARE_BOOT_UNVERIFIED', body)

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


class SignedOTAPipeline(unittest.TestCase):
    def setUp(self):
        if not shutil.which('openssl') or not shutil.which('mksquashfs'):
            self.skipTest('OpenSSL and mksquashfs are required')
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.work = Path(workspace.name)
        source = self.work / 'source'
        source.mkdir()
        (source / 'gaoos-version').write_text('20261120\n')
        self.artifact = self.work / 'system.squashfs'
        subprocess.run(['mksquashfs', source, self.artifact, '-noappend', '-processors', '1', '-quiet'],
                       check=True, capture_output=True)
        self.key = subprocess.check_output(['openssl', 'genpkey', '-algorithm', 'Ed25519'], text=True)
        self.report = self.work / 'report.json'
        self.output = self.work / 'signed-ota.json'
        self.ota = {
            'board': 'powkiddy,rgb30', 'arch': 'aarch64', 'boot_layout': 'gaoos-rk3566-ab-v1',
            'sequence': 10, 'version': '20261120', 'git_sha': 'a' * 40, 'kernel_sha256': 'b' * 64,
            'dtb_sha256': 'c' * 64, 'sha256': hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
            'size_bytes': self.artifact.stat().st_size
        }

    def sign(self, status='PASS', extra=()):
        self.report.write_text(json.dumps({'status': status, 'ota': self.ota}))
        return subprocess.run([
            'python3', ROOT / '.github/scripts/sign-ota-release.py', '--report', self.report,
            '--artifact', self.artifact, '--output', self.output, '--commit', 'a' * 40,
            '--channel', 'nightly', '--sequence', '10', '--issued-at', '1700000000',
            '--expires-at', '1700086400', *extra
        ], env={**os.environ, 'GAOOS_OTA_SIGNING_KEY': self.key}, capture_output=True, text=True)

    def test_signed_bytes_match_report_and_verify_as_ed25519(self):
        result = self.sign(extra=('--rollback-from', '12,11'))
        self.assertEqual(result.returncode, 0, result.stderr)
        first = self.output.read_bytes()
        envelope = json.loads(first)
        manifest = base64.urlsafe_b64decode(envelope['manifest'] + '=' * (-len(envelope['manifest']) % 4))
        signature = base64.urlsafe_b64decode(envelope['signature'] + '==')
        public = subprocess.check_output(['openssl', 'pkey', '-pubout', '-outform', 'DER'],
                                         input=self.key.encode())
        self.assertEqual(envelope['key_id'], hashlib.sha256(public[-32:]).hexdigest())
        self.assertEqual(json.loads(manifest), {
            **self.ota, 'channel': 'nightly', 'schema_version': 1, 'minimum_sequence': 0,
            'issued_at': 1700000000, 'expires_at': 1700086400, 'rollback_from': [11, 12]
        })
        for name, contents in [('manifest', manifest), ('signature', signature), ('public', public)]:
            (self.work / name).write_bytes(contents)
        subprocess.run(['openssl', 'pkeyutl', '-verify', '-rawin', '-pubin', '-keyform', 'DER',
                        '-inkey', self.work / 'public', '-in', self.work / 'manifest',
                        '-sigfile', self.work / 'signature'], check=True, capture_output=True)
        result = self.sign(extra=('--rollback-from', '11,12'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.output.read_bytes(), first)

    def test_signing_requires_matching_artifact_and_validated_build(self):
        for status, extra in [
            ('FAIL', ()), ('PASS', ('--commit', 'f' * 40)), ('PASS', ('--sequence', '11')),
            ('PASS', ('--minimum-sequence', '10')), ('PASS', ('--rollback-from', '9')),
            ('PASS', ('--rollback-from', '11,11')), ('PASS', ('--expires-at', '1710000000'))
        ]:
            with self.subTest(status=status, extra=extra):
                self.assertNotEqual(self.sign(status, extra).returncode, 0)
                self.assertFalse(self.output.exists())
        contents = self.artifact.read_bytes()
        self.artifact.write_bytes(contents[:-1] + bytes([contents[-1] ^ 1]))
        result = self.sign()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Artifact SHA256 does not match validation', result.stderr)
        self.assertFalse(self.output.exists())


    def test_registration_requires_storage_verified_exact_bytes(self):
        if not shutil.which('jq'):
            self.skipTest('jq is required')
        self.assertEqual(self.sign().returncode, 0)
        binaries = self.work / 'bin'
        binaries.mkdir()
        log = self.work / 'publication-log'
        head = self.work / 'head.json'
        for name, script in {
            'aws': '''#!/bin/bash
printf '%s\n' "$*" >> "$COMMAND_LOG"
if [[ "$4" == put-object ]]; then
  exit 1
fi
cat "$HEAD_RESPONSE"
''',
            'curl': '''#!/bin/bash
IFS= read -r authorization
[[ "$authorization" == 'Authorization: Bearer test-publication-token' ]] || exit 1
printf '%s\n' "$*" >> "$COMMAND_LOG"
'''
        }.items():
            command = binaries / name
            command.write_text(script)
            command.chmod(0o700)
        env = {
            **os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
            'COMMAND_LOG': str(log), 'HEAD_RESPONSE': str(head),
            'GAOOS_OTA_S3_ENDPOINT': 'https://s3.example', 'GAOOS_OTA_S3_BUCKET': 'artifacts',
            'GAOOS_OTA_API_URL': 'https://ota.example', 'GAOOS_OTA_PUBLICATION_TOKEN': 'test-publication-token'
        }
        digest = base64.b64encode(bytes.fromhex(self.ota['sha256'])).decode('ascii')
        script = ROOT / '.github/scripts/register-ota-release.sh'
        for response, should_register in [
            ({'ChecksumSHA256': digest, 'ContentLength': self.ota['size_bytes'] + 1}, False),
            ({'ChecksumSHA256': 'invalid', 'ContentLength': self.ota['size_bytes']}, False),
            ({'ChecksumSHA256': digest, 'ContentLength': self.ota['size_bytes'], 'ChecksumType': 'COMPOSITE'}, False),
            ({'ChecksumSHA256': digest, 'ContentLength': self.ota['size_bytes'], 'ChecksumType': 'FULL_OBJECT'}, True)
        ]:
            with self.subTest(response=response):
                log.write_text('')
                head.write_text(json.dumps(response))
                result = run_script(script, [self.artifact, self.report, self.output], self.work, env=env)
                self.assertEqual(result.returncode == 0, should_register, result.stderr)
                calls = log.read_text()
                self.assertIn(f'--key sha256/{self.ota["sha256"]}.squashfs', calls)
                self.assertIn('--if-none-match *', calls)
                self.assertIn('--checksum-mode ENABLED', calls)
                self.assertEqual('https://ota.example/api/v1/operator/releases' in calls, should_register)
                self.assertNotIn('test-publication-token', calls + result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
