#!/usr/bin/env python3
"""Run early-boot logging and fatal handling without mounting host filesystems."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
INIT = Path(os.environ.get('GAOOS_INIT_SCRIPT', ROOT / 'projects/ROCKNIX/packages/sysutils/busybox/scripts/init'))
SOURCE = INIT.read_text()


def function(name):
    start = SOURCE.index(name + '() {')
    return SOURCE[start:SOURCE.index('\n}', start) + 2]


def run(code):
    return subprocess.run(['bash', '-s'], input='enable_boot_console() { :; }\n' + code, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=5)


class EarlyBootDiagnostics(unittest.TestCase):
    def test_console_stderr_and_ram_log_survive_without_storage(self):
        with tempfile.TemporaryDirectory() as td:
            start = function('start_boot_logging').replace('/run/', td + '/')
            console = Path(td) / 'console'
            start = start.replace('/dev/console', str(console))
            result = run(start + '\n' + function('stop_boot_logging') + '''
TEE_PID=""
start_boot_logging
exec 3>&1
printf 'stdout evidence\\n'
printf 'stderr evidence\\n' >&2
stop_boot_logging %s
''' % shlex.quote(str(console)))
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((Path(td) / 'initramfs.log').read_text(),
                             'stdout evidence\nstderr evidence\n')
            self.assertIn('stderr evidence', console.read_text())
            self.assertFalse((Path(td) / 'initramfs.pipe').exists())

    def test_log_persists_only_on_mounted_storage(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / 'ram.log'
            log.write_text('failure before systemd\n')
            storage = Path(td) / 'storage'
            storage.mkdir()
            for mounted in (False, True):
                result = run(function('persist_boot_log').replace('/usr/bin/busybox mountpoint', 'mountpoint') + '''
mountpoint() { return %s; }
persist_boot_log %s %s
''' % (0 if mounted else 1, shlex.quote(str(storage)), shlex.quote(str(log))))
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual((storage / 'init.log').exists(), mounted)
            self.assertEqual((storage / 'init.log').read_text(), log.read_text())

    def test_production_ab_failure_does_not_open_shell(self):
        self.assert_error_shell(ab=True, debug=False, expected=False)

    def test_production_without_slot_arguments_uses_compiled_ab_mode(self):
        assignment = next(line for line in SOURCE.splitlines() if line.startswith('GAOOS_AB_MODE='))
        result = run(assignment.replace('@GAOOS_AB_BOOT@', 'yes') + '\n' + function('error') + '''
DEBUG="" BREAK="" BOOT_STEP=mount_flash
unset GAOOS_SLOT GAOOS_DEV
debug_shell() { echo UNSAFE_SHELL; }
error mount 'boot device unavailable'
''')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn('UNSAFE_SHELL', result.stdout)

    def test_debug_ab_failure_opens_local_shell(self):
        self.assert_error_shell(ab=True, debug=True, expected=True)

    def test_upstream_error_shell_behavior_is_preserved(self):
        self.assert_error_shell(ab=False, debug=False, expected=True)

    def assert_error_shell(self, ab, debug, expected):
        result = run(function('error') + '''
GAOOS_AB_MODE=%s DEBUG=%s BOOT_STEP=mount_sysroot
BREAK=""
debug_shell() { echo SHELL; }
error mount 'corrupt SquashFS'
''' % ('yes' if ab else 'no', 'yes' if debug else 'no'))
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('corrupt SquashFS', result.stdout)
        self.assertEqual('SHELL' in result.stdout, expected)

    def test_production_failure_reboots_and_never_continues_if_reboot_fails(self):
        self.assert_fatal_reboot(debug=False, expected=True)

    def test_debug_failure_stops_without_reboot(self):
        self.assert_fatal_reboot(debug=True, expected=False)

    def assert_fatal_reboot(self, debug, expected):
        result = run(function('fatal_ab_boot') + '''
DEBUG=%s BREAK="" BOOT_STEP=mount_sysroot
GAOOS_SLOT=b SLOT_DEV=/dev/mmcblk10p3 GAOOS_FLASH_DEV=/dev/mmcblk10p1
boot_log() { echo "$*"; }
cat() { echo MOUNTS; }
dmesg() { echo KERNEL; }
persist_boot_log() { echo PERSIST; }
stop_boot_logging() { echo DRAIN; }
debug_shell() { echo DEBUG_SHELL; }
sleep() { echo "SLEEP $1"; if [ "$1" = 60 ]; then exit 0; fi; }
sync() { :; }
reboot() { echo REBOOT; return 1; }
fatal_ab_boot
echo UNSAFE_CONTINUATION
''' % ('yes' if debug else 'no'))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual('\nREBOOT\n' in result.stdout, expected)
        self.assertEqual('SLEEP 10' in result.stdout, expected)
        self.assertEqual('DEBUG_SHELL' in result.stdout, debug)
        self.assertLess(result.stdout.index('DRAIN'), result.stdout.index('PERSIST'))
        self.assertIn('SLEEP 60', result.stdout)
        self.assertIn('slot=b', result.stdout)
        self.assertNotIn('UNSAFE_CONTINUATION', result.stdout)

    def test_recovery_skips_nonexistent_event_devices(self):
        with tempfile.TemporaryDirectory() as td:
            check_key = function('is_key_pressed').replace('/dev/input/', td + '/')
            result = run(check_key + '''
evtest() { echo UNEXPECTED_EVTEST; return 1; }
is_key_pressed 114
''')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertNotIn('UNEXPECTED_EVTEST', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
