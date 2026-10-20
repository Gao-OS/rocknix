#!/usr/bin/env python3
"""Exercise console policy and logging without mounting host filesystems."""
from pathlib import Path
import os
import shlex
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[4]
INIT = Path(os.environ.get('GAOOS_INIT_SCRIPT', ROOT / 'projects/ROCKNIX/packages/sysutils/busybox/scripts/init'))
SOURCE = INIT.read_text()


def function(name):
    start = SOURCE.index(name + '() {')
    return SOURCE[start:SOURCE.index('\n}', start) + 2]


class QuietVerboseBoot(unittest.TestCase):
    def run_boot(self, program, names, *, timeout=5):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            console = work / 'console'
            console.touch()
            (work / 'printk').touch()
            code = '\n'.join(function(name) for name in names)
            code = code.replace('/run/', td + '/').replace('/dev/console', str(console))
            code = code.replace('/proc/sys/kernel/printk', str(work / 'printk'))
            result = subprocess.run(['bash', '-s'], input='''
GAOOS_BOOT=yes GAOOS_VERBOSE=no BOOT_CONSOLE=no TEE_PID=""
DEBUG="" BREAK="" PROGRESS="" INIT_ARGS=""
clear() { :; }
''' + code + '\n' + program.replace('@CONSOLE@', shlex.quote(str(console))),
                                    text=True, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            log = (work / 'initramfs.log').read_text() if (work / 'initramfs.log').exists() else ''
            self.assertNotIn('command not found', result.stdout + log)
            return result, console.read_text(), log, (work / 'printk').read_text()

    def test_quiet_records_stdout_stderr_and_fd3_without_console(self):
        result, console, log, _ = self.run_boot('''
start_boot_logging
echo stdout
echo stderr >&2
echo diagnostic >&3
stop_boot_logging @CONSOLE@
''', ['start_boot_logging', 'stop_boot_logging'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(console, '')
        self.assertEqual(result.stdout, '')
        self.assertEqual(log, 'stdout\nstderr\ndiagnostic\n')

    def test_verbose_replays_once_and_drains_all_writers(self):
        result, console, log, _ = self.run_boot('''
start_boot_logging
echo buffered
enable_boot_console
enable_boot_console
echo subsequent
echo last-error >&2
echo last-diagnostic >&3
stop_boot_logging @CONSOLE@
[ -z "$TEE_PID" ] && [ ! -e /run/initramfs.pipe ]
''', ['start_boot_logging', 'stop_boot_logging', 'enable_boot_console'])
        self.assertEqual(result.returncode, 0, result.stdout)
        expected = 'buffered\nsubsequent\nlast-error\nlast-diagnostic\n'
        self.assertEqual(log, expected)
        self.assertEqual(console, expected)

    def test_fatal_error_reveals_console_and_records_without_shell(self):
        result, console, log, _ = self.run_boot('''
GAOOS_AB_MODE=yes BOOT_STEP=mount_sysroot
clear() { :; }
debug_shell() { echo UNSAFE_SHELL; }
start_boot_logging
echo early-evidence
error mount 'corrupt SquashFS'
stop_boot_logging @CONSOLE@
''', ['start_boot_logging', 'stop_boot_logging', 'enable_boot_console', 'error'])
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('corrupt SquashFS', console)
        self.assertIn('early-evidence', console)
        self.assertEqual(console, log)
        self.assertNotIn('UNSAFE_SHELL', console)

    def assert_mode(self, arguments='', held=False):
        result, console, log, printk = self.run_boot('''
%s
detect_gaoos_start() { return %s; }
dmesg() { echo buffered-kernel; }
clear() { :; }
start_boot_logging
echo before-selection
select_boot_mode
echo "VERBOSE=$GAOOS_VERBOSE DEBUG=$DEBUG BREAK=$BREAK PROGRESS=$PROGRESS ARGS=$INIT_ARGS"
stop_boot_logging @CONSOLE@
''' % (arguments, 0 if held else 1), ['start_boot_logging', 'stop_boot_logging', 'enable_boot_console', 'select_boot_mode', 'configure_systemd_boot_output', 'boot_log'])
        self.assertEqual(result.returncode, 0, result.stdout)
        return console, log, printk

    def test_normal_boot_defaults_quiet(self):
        console, log, _ = self.assert_mode()
        self.assertEqual(console, '')
        self.assertIn('VERBOSE=no DEBUG= BREAK=', log)
        self.assertIn('--show-status=error', log)

    def test_held_start_enables_logs_and_systemd_without_debug(self):
        console, log, printk = self.assert_mode(held=True)
        self.assertIn('buffered-kernel', console)
        self.assertIn('VERBOSE=yes DEBUG= BREAK= PROGRESS=yes', console)
        self.assertIn('--show-status=yes', console)
        self.assertTrue(printk.startswith('8 '), printk)
        self.assertEqual(log, console)

    def test_explicit_verbose_override(self):
        console, _, _ = self.assert_mode('GAOOS_VERBOSE=yes\ndetect_gaoos_start() { echo UNEXPECTED_PROBE; return 1; }')
        self.assertIn('VERBOSE=yes', console)
        self.assertNotIn('UNEXPECTED_PROBE', console)

    def test_progress_debug_and_break_still_request_console(self):
        for argument in ('PROGRESS=yes', 'DEBUG=yes', 'BREAK=mount_sysroot'):
            with self.subTest(argument=argument):
                console, _, _ = self.assert_mode(argument)
                self.assertIn('before-selection', console)
                self.assertIn('--show-status=yes', console)

    def test_normal_splash_is_gaoos_and_verbose_skips_it(self):
        for visible in (False, True):
            code = function('load_splash').replace('/usr/bin/gaoos-splash', 'gaoos_splash')
            code = code.replace('/usr/bin/rocknix-splash', 'rocknix_splash')
            result = subprocess.run(['bash', '-s'], text=True, input=code + '''
GAOOS_BOOT=yes GAOOS_VERBOSE=%s BOOT_CONSOLE=%s
gaoos_splash() { echo GAOOS_LOGO; }
rocknix_splash() { echo WRONG_LOGO; }
wait_for_framebuffer() { :; }
load_splash
''' % ('yes' if visible else 'no', 'yes' if visible else 'no'),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.strip(), '' if visible else 'GAOOS_LOGO')

    def test_explicit_kernel_override_is_parsed_independently_of_debug(self):
        parser = SOURCE[SOURCE.index('# parse command line arguments'):SOURCE.index('# Presence of the bootloader')]
        result = subprocess.run(['bash', '-s'], text=True, input=parser.replace('$(cat /proc/cmdline)', 'gaoos.verbose=1') + '\necho "$GAOOS_VERBOSE:$DEBUG:$BREAK"\n',
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=5)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout.strip(), 'yes::')

    def test_detection_precedes_splash_after_module_initialization(self):
        sequence = SOURCE[SOURCE.index('# main boot sequence'):SOURCE.index('BOOT_STEP=final')]
        self.assertLess(sequence.index('load_modules'), sequence.index('select_boot_mode'))
        self.assertLess(sequence.index('select_boot_mode'), sequence.index('load_splash'))

    def test_volume_recovery_reveals_its_interactive_prompt(self):
        code = function('recovery_mode') + '''
FORCE_RECOVERY=no
stty() { :; }
is_key_pressed() { return 0; }
enable_boot_console() { echo VISIBLE; }
StartProgress() { echo PROMPT; exit 0; }
recovery_mode
'''
        result = subprocess.run(['bash', '-s'], input=code, text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'VISIBLE\nPROMPT\n')

    def test_framebuffer_wait_is_bounded_and_stops_when_ready(self):
        with tempfile.TemporaryDirectory() as td:
            framebuffer = Path(td) / 'fb0'
            code = function('wait_for_framebuffer').replace('/dev/fb0', str(framebuffer))
            code += '\nusleep() { sleep 0.05; }\nwait_for_framebuffer\n'
            before = time.monotonic()
            subprocess.run(['bash', '-c', code], check=True, timeout=2)
            self.assertLess(time.monotonic() - before, 1.8)
            timer = threading.Timer(0.1, framebuffer.touch)
            timer.start()
            try:
                before = time.monotonic()
                subprocess.run(['bash', '-c', code], check=True, timeout=2)
                self.assertLess(time.monotonic() - before, 0.7)
            finally:
                timer.join()


if __name__ == '__main__':
    unittest.main(verbosity=2)
