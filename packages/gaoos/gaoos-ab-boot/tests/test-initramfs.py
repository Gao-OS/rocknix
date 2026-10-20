#!/usr/bin/env python3
"""Exercise A/B mount selection with initramfs-configured command availability."""
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
BUSYBOX = ROOT / 'projects/ROCKNIX/packages/sysutils/busybox'


class Initramfs(unittest.TestCase):
    def setUp(self):
        self.options = {
            line.split('=', 1)[0]
            for line in (BUSYBOX / 'config/busybox-init.conf').read_text().splitlines()
            if line.endswith('=y')
        }
        source = (BUSYBOX / 'scripts/init').read_text()
        start = source.index('mount_sysroot() {')
        self.mount_sysroot = source[start:source.index('\n}', start) + 2]

    def run_mount(self, flash, slot, options, block_exists=True, mount_status=0,
                  hook=None, ab_mode='yes', metadata=False):
        # Model compiled applet availability, using the host implementation only
        # when the init configuration provides that command and its -f option.
        readlink = ''
        if 'CONFIG_READLINK' in options:
            host_readlink = shlex.quote(shutil.which('readlink'))
            follow_check = ''
            if 'CONFIG_FEATURE_READLINK_FOLLOW' not in options:
                follow_check = '''
  if builtin [ "$1" = -f ]; then
    printf 'readlink: invalid option -- f\\n' >&2
    return 1
  fi
'''
            readlink = 'readlink() {\n' + follow_check + host_readlink + ' "$@";\n}\n'
        code = '''
PATH=/nonexistent-initramfs-test-path
GAOOS_SLOT=%s
GAOOS_AB_MODE=%s
progress() { :; }
boot_log() { printf 'BOOT %%s\\n' "$*"; }
awk() { printf '%%s\\n' %s; }
# Device nodes are supplied by the kernel on hardware; model both slot nodes.
[() {
  if builtin [ "$1" = -b ]; then return %s; fi
  if builtin [ "$1" = ! ] && builtin [ "$2" = -b ]; then return %s; fi
  if builtin [ "$1" = -f ] && builtin [ "$2" = /flash/ab_state.env ]; then return %s; fi
  builtin [ "$@"
}
mount() { printf 'MOUNT %%s\\n' "$*"; return %s; }
usleep() { :; }
mount_part() { printf 'LEGACY MOUNT\\n'; return 0; }
error() { printf 'ERROR: %%s: %%s\\n' "$1" "$2"; return 1; }
''' % (shlex.quote(slot), shlex.quote(ab_mode), shlex.quote(str(flash)),
       0 if block_exists else 1, 1 if block_exists else 0,
       0 if metadata else 1, mount_status)
        mount_sysroot = self.mount_sysroot
        if hook:
            mount_sysroot = mount_sysroot.replace('/flash/post-sysroot.sh', str(hook))
        return subprocess.run(
            ['/bin/bash', '--noprofile', '--norc', '-s'],
            input=code + readlink + mount_sysroot + '\nmount_sysroot\n',
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )

    def assert_slot_mounted(self, result, device):
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('MOUNT -t squashfs -o ro ' + device + ' /sysroot', result.stdout)
        self.assertNotIn('LEGACY', result.stdout)
        self.assertNotIn('ERROR', result.stdout)

    def test_direct_mmc_path_mounts_slot_a(self):
        result = self.run_mount('/dev/mmcblk1p1', 'a', self.options)
        self.assert_slot_mounted(result, '/dev/mmcblk1p2')

    def test_boot_device_symlink_mounts_slot_b(self):
        with tempfile.TemporaryDirectory() as td:
            flash = Path(td) / 'boot-device'
            flash.symlink_to('/dev/mmcblk0p1')
            result = self.run_mount(flash, 'b', self.options)
        self.assert_slot_mounted(result, '/dev/mmcblk0p3')

    def test_missing_readlink_cannot_use_host_path(self):
        result = self.run_mount('/dev/mmcblk1p1', 'a',
                                self.options - {'CONFIG_READLINK'})
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('readlink: command not found', result.stdout)
        self.assertIn('Cannot identify A/B boot disk', result.stdout)
        self.assertNotIn('MOUNT ', result.stdout)

    def test_readlink_without_follow_cannot_mount_slot(self):
        options = (self.options | {'CONFIG_READLINK'}) - {'CONFIG_FEATURE_READLINK_FOLLOW'}
        result = self.run_mount('/dev/mmcblk1p1', 'a', options)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('readlink: invalid option -- f', result.stdout)
        self.assertIn('Cannot identify A/B boot disk', result.stdout)
        self.assertNotIn('MOUNT ', result.stdout)


    def test_invalid_slot_is_rejected(self):
        result = self.run_mount('/dev/mmcblk0p1', 'invalid', self.options)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('Invalid A/B slot', result.stdout)
        self.assertNotIn('MOUNT ', result.stdout)

    def test_missing_slot_does_not_mount_legacy_system_on_ab_disk(self):
        for ab_mode, metadata in [('yes', False), ('no', True)]:
            result = self.run_mount('/dev/mmcblk0p1', '', self.options,
                                    ab_mode=ab_mode, metadata=metadata)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn('Invalid A/B slot: missing', result.stdout)
            self.assertNotIn('MOUNT ', result.stdout)

    def test_non_ab_boot_preserves_legacy_mount(self):
        result = self.run_mount('/dev/mmcblk0p1', '', self.options, ab_mode='no')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('LEGACY MOUNT', result.stdout)

    def test_multiple_mmc_indexes_and_existing_non_mmc_paths(self):
        for flash, root in [('/dev/mmcblk10p1', '/dev/mmcblk10p3'),
                            ('/dev/loop12p1', '/dev/loop12p3'),
                            ('/dev/nvme0n1p1', '/dev/nvme0n1p3'),
                            ('/dev/nvme1n1p1', '/dev/nvme1n1p3'),
                            ('/dev/sda1', '/dev/sda3')]:
            self.assert_slot_mounted(self.run_mount(flash, 'b', self.options), root)

    def test_post_mount_hook_failure_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            hook = Path(td) / 'post-sysroot.sh'
            hook.write_text('return 1\n')
            result = self.run_mount('/dev/mmcblk0p1', 'a', self.options, hook=hook)
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_invalid_mmc_device_is_rejected(self):
        for flash in ('/dev/mmcblkXp1', '/dev/mmcblk0p11', '', '/dev/mmcblk0',
                      '/dev/sda11', '/dev/nvmen1p1', '/dev/nvme0p1', '/dev/loopXp1'):
            with self.subTest(flash=flash):
                result = self.run_mount(flash, 'a', self.options)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn('Cannot identify A/B boot disk', result.stdout)
                self.assertNotIn('MOUNT ', result.stdout)

    def test_missing_slot_partition_fails_without_legacy_fallback(self):
        result = self.run_mount('/dev/mmcblk10p1', 'b', self.options,
                                block_exists=False)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('A/B slot device unavailable', result.stdout)
        self.assertNotIn('LEGACY', result.stdout)

    def test_corrupt_squashfs_mount_failure_is_not_success(self):
        result = self.run_mount('/dev/mmcblk0p1', 'a', self.options,
                                mount_status=1)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('Cannot mount A/B slot', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
