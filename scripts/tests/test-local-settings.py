#!/usr/bin/env python3
"""Check local build settings using isolated files and a fake Docker executable."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
KEYS = ['LOCAL_WIFI_SSID', 'LOCAL_WIFI_KEY', 'LOCAL_SSH_KEYS_FILE']


class LocalSettings(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gaoos-local-settings-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.test_home = self.root / 'test-home'
        self.test_home.mkdir()
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.test_home),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        self.key_file = self.root / 'dummy $literal # keys.pub'
        self.key_file.write_text('dummy public key fixture\n')
        self.values = dict(zip(KEYS, ['Dummy Network $ # tag',
                                     'dummy literal$ # passphrase', str(self.key_file)]))
        self.local = self.root / 'local-setting.env'

    def write_settings(self, path, values, export=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(('export ' if export else '') + key + '=' +
                                shlex.quote(value) + '\n' for key, value in values.items()))

    def run_command(self, args, **kwargs):
        return subprocess.run(args, cwd=self.root, env=self.env, text=True,
                              capture_output=True, **kwargs)

    def native_source(self):
        source = (ROOT / 'config/options').read_text()
        source = source[source.index('# read local persistent options'):
                        source.index('if [ "${LOCAL_CCACHE_SUPPORT}"')]
        return 'ROOT=' + shlex.quote(str(self.root)) + '\n' + source

    def native_settings(self):
        child = ('import json, os; print(json.dumps('
                 '{key: os.environ.get(key) for key in ' + repr(KEYS) + '}))')
        code = self.native_source()
        code += '\n' + shlex.quote(sys.executable) + ' -c ' + shlex.quote(child) + '\n'
        result = self.run_command(['bash', '-s'], input=code)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def install_get_env(self):
        scripts = self.root / 'scripts'
        scripts.mkdir(exist_ok=True)
        target = scripts / 'get_env'
        shutil.copyfile(ROOT / 'scripts/get_env', target)
        target.chmod(0o755)
        return target

    @staticmethod
    def parse_env(text):
        return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)

    def test_native_exports_local_assignments_to_child_process(self):
        self.write_settings(self.local, self.values)
        before = self.local.read_bytes()
        self.assertEqual(self.native_settings(), self.values)
        self.assertEqual(self.local.read_bytes(), before)

    def test_local_settings_win_over_legacy_files_and_environment(self):
        self.env.update({key: 'environment fixture' for key in KEYS})
        self.write_settings(self.root / '.gaoos/options',
                            {key: 'repository legacy fixture' for key in KEYS}, export=True)
        self.write_settings(self.test_home / '.gaoos/options',
                            {key: 'global legacy fixture' for key in KEYS}, export=True)
        self.write_settings(self.local, self.values)
        self.assertEqual(self.native_settings(), self.values)

    def test_missing_local_file_preserves_legacy_precedence(self):
        self.write_settings(self.root / '.gaoos/options',
                            {key: 'repository legacy fixture' for key in KEYS}, export=True)
        legacy = {key: 'global legacy fixture' for key in KEYS}
        self.write_settings(self.test_home / '.gaoos/options', legacy, export=True)
        self.assertEqual(self.native_settings(), legacy)

    def test_get_env_emits_local_values_without_rewriting_file(self):
        self.write_settings(self.local, self.values)
        before = self.local.read_bytes()
        result = self.run_command([str(self.install_get_env())])
        self.assertEqual(result.returncode, 0, result.stderr)
        emitted = self.parse_env(result.stdout)
        self.assertEqual({key: emitted.get(key) for key in KEYS}, self.values)
        self.assertEqual(self.local.read_bytes(), before)

    def test_get_env_preserves_environment_when_local_file_is_missing(self):
        self.env.update(self.values)
        result = self.run_command([str(self.install_get_env())])
        self.assertEqual(result.returncode, 0, result.stderr)
        emitted = self.parse_env(result.stdout)
        self.assertEqual({key: emitted.get(key) for key in KEYS}, self.values)

    def stage_package(self, values):
        self.write_settings(self.local, values)
        package = self.root / 'package'
        config = package / 'config/system/configs'
        config.mkdir(parents=True)
        (config / 'system.cfg').write_text('wifi.enabled=0\n')
        update = package / 'update.sh'
        update.write_text('#!/bin/sh\n')
        install = self.root / 'install'
        code = self.native_source() + f'''
INSTALL={shlex.quote(str(install))}
PKG_DIR={shlex.quote(str(package))}
rsync() {{ shift; cp -a "$@"; }}
find_file_path() {{ FOUND_PATH={shlex.quote(str(update))}; }}
. {shlex.quote(str(ROOT / 'projects/ROCKNIX/packages/rocknix/package.mk'))}
makeinstall_target
'''
        result = self.run_command(['bash', '-se'], input=code)
        self.assertEqual(result.returncode, 0, result.stderr)
        return install

    def test_package_stages_authorized_keys_from_a_quoted_filename(self):
        install = self.stage_package({'LOCAL_SSH_KEYS_FILE': str(self.key_file)})
        self.assertEqual((install / 'usr/config/ssh/authorized_keys').read_bytes(),
                         self.key_file.read_bytes())

    def test_package_stages_ssid_profile_and_literal_passphrase(self):
        install = self.stage_package({key: self.values[key] for key in KEYS[:2]})
        profile = install / 'usr/config/iwd' / (self.values['LOCAL_WIFI_SSID'] + '.psk')
        self.assertEqual(profile.read_text(), '[Security]\nPassphrase=' +
                         self.values['LOCAL_WIFI_KEY'] + '\n')
        self.assertEqual((install / 'usr/config/system/configs/system.cfg').read_text(),
                         'wifi.enabled=1\n')

    def run_docker(self):
        self.install_get_env()
        shutil.copyfile(ROOT / 'Makefile', self.root / 'Makefile')
        binaries = self.root / 'bin'
        binaries.mkdir()
        docker = binaries / 'docker'
        capture = self.root / 'docker.json'
        docker.write_text('#!' + sys.executable + '''
import json, os, pathlib, sys
if sys.argv[1:] == ['--version']:
    print('Docker version fixture')
elif sys.argv[1] == 'run':
    args = sys.argv[1:]
    env_file = pathlib.Path(args[args.index('--env-file') + 1])
    values = dict(line.split('=', 1) for line in env_file.read_text().splitlines() if '=' in line)
    pathlib.Path(os.environ['DOCKER_CAPTURE']).write_text(json.dumps({'args': args, 'env': values}))
''')
        docker.chmod(0o755)
        self.env['PATH'] = str(binaries) + os.pathsep + self.env['PATH']
        self.env['DOCKER_CAPTURE'] = str(capture)
        result = self.run_command(['make', '--no-print-directory', 'docker-shell'])
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(capture.read_text())

    def assert_key_mount(self, recorded, key_file):
        mount = str(key_file) + ':' + str(key_file)
        self.assertTrue(any(recorded['args'][index:index + 2] == ['-v', mount]
                            for index in range(len(recorded['args']) - 1)))

    def test_docker_uses_local_values_and_mounts_the_original_key_path(self):
        self.write_settings(self.local, self.values)
        before = self.local.read_bytes()
        recorded = self.run_docker()
        self.assertEqual({key: recorded['env'].get(key) for key in KEYS}, self.values)
        self.assert_key_mount(recorded, self.key_file)
        self.assertEqual(recorded['args'][-1], 'bash')
        self.assertEqual(self.local.read_bytes(), before)

    def test_docker_preserves_nonexported_legacy_key_setting(self):
        key_file = self.root / 'legacy keys.pub'
        key_file.write_text('dummy legacy public key\n')
        options = self.test_home / '.GaoOS/options'
        options.parent.mkdir()
        options.write_text('LOCAL_SSH_KEYS_FILE=' + str(key_file) + '\n')
        recorded = self.run_docker()
        self.assertEqual(recorded['env'].get('LOCAL_SSH_KEYS_FILE'), str(key_file))
        self.assert_key_mount(recorded, key_file)

    def test_private_settings_are_ignored_but_example_is_not(self):
        shutil.copyfile(ROOT / '.gitignore', self.root / '.gitignore')
        result = self.run_command(['git', 'init', '--quiet'])
        self.assertEqual(result.returncode, 0, result.stderr)
        private = self.run_command(['git', 'check-ignore', '--no-index', 'local-setting.env'])
        example = self.run_command(['git', 'check-ignore', '--no-index', 'local-setting.example.env'])
        self.assertEqual(private.returncode, 0, 'Private settings must be ignored')
        self.assertEqual(example.returncode, 1, 'Example must remain trackable')

    def test_example_contains_no_active_credentials(self):
        example = ROOT / 'local-setting.example.env'
        self.assertTrue(example.is_file(), 'Tracked settings example is missing')
        self.write_settings(self.local, {})
        shutil.copyfile(example, self.local)
        actual = self.native_settings()
        self.assertTrue(all(value in (None, '') for value in actual.values()),
                        'Example must keep all credentials commented or empty')


if __name__ == '__main__':
    unittest.main()
