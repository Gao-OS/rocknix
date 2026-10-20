#!/usr/bin/env python3
"""Exercise RGB30-only early START selection with a synthetic sysfs tree."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[4]
BUSYBOX = ROOT / 'projects/ROCKNIX/packages/sysutils/busybox'
HELPER = BUSYBOX / 'scripts/gaoos-boot-mode'


class StartSelection(unittest.TestCase):
    def detect(self, *, compatible=b'powkiddy,rgb30\0rockchip,rk3566\0',
               state='held', device=True, name='retrogame_joypad',
               phys='rocknix-singleadc-joypad/input0', usb=False):
        self.assertTrue(HELPER.is_file(), 'RGB30 early START helper is missing')
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            dt = root / 'sys/firmware/devicetree/base'
            dt.mkdir(parents=True)
            (dt / 'compatible').write_bytes(compatible)
            inputs = root / 'sys/class/input'
            inputs.mkdir(parents=True)
            if device:
                path = ('sys/devices/platform/usb/controller/input/input0' if usb else
                        'sys/devices/platform/rocknix-singleadc-joypad/input/input0')
                onboard = root / path
                onboard.mkdir(parents=True)
                (onboard / 'name').write_text(name + '\n')
                (onboard / 'phys').write_text(phys + '\n')
                event = inputs / 'event4'
                event.mkdir()
                (event / 'device').symlink_to(onboard)
                dev = root / 'dev/input'
                dev.mkdir(parents=True)
                (dev / 'event4').symlink_to('/dev/null')
            # Only platform paths are substituted. The shell function and sysfs
            # filtering run as shipped; evtest ioctl is the hardware boundary.
            source = HELPER.read_text().replace('/sys/', td + '/sys/').replace('/dev/input/', td + '/dev/input/')
            source = source.replace('/usr/bin/busybox', 'bb')
            code = '''
bb() { if [ "$1" = usleep ]; then sleep 0.05; else "$@"; fi; }
queries=0
is_key_pressed() {
  queries=$((queries + 1))
  printf '%%s %%s\\n' "$1" "$2" >> %s
  [ "$1" = 315 ] || return 1
  case %s in
    held) return 0;;
    released|other_button|error) return 1;;
    bounced) [ "$queries" -ne 6 ];;
  esac
}
''' % (shlex.quote(str(root / 'queries')), shlex.quote(state))
            start = time.monotonic()
            result = subprocess.run(['bash', '-s'], input=code + source + '\ndetect_gaoos_start\n',
                                    text=True, capture_output=True, timeout=3)
            elapsed = time.monotonic() - start
            queries = (root / 'queries').read_text().splitlines() if (root / 'queries').exists() else []
            self.assertEqual(result.stdout + result.stderr, '')
            return result.returncode, elapsed, queries

    def test_only_start_held_for_500ms_selects_verbose(self):
        code, elapsed, queries = self.detect()
        self.assertEqual(code, 0)
        self.assertGreaterEqual(elapsed, 0.5)
        self.assertEqual(len(queries), 11)
        self.assertTrue(all(q.startswith('315 ') and q.endswith('/dev/input/event4') for q in queries))

    def test_release_bounce_other_button_and_query_failure_stay_quiet(self):
        for state in ('released', 'bounced', 'other_button', 'error'):
            with self.subTest(state=state):
                self.assertNotEqual(self.detect(state=state)[0], 0)

    def test_other_boards_never_wait_or_query(self):
        for compatible in (b'powkiddy,rgb20sx\0rockchip,rk3566\0',
                           b'powkiddy,rgb30-other\0', b''):
            code, elapsed, queries = self.detect(compatible=compatible)
            self.assertNotEqual(code, 0)
            self.assertLess(elapsed, 0.2)
            self.assertFalse(queries)

    def test_usb_and_wrong_input_identity_cannot_select_verbose(self):
        for kwargs in ({'usb': True}, {'name': 'USB gamepad'}, {'phys': 'usb/input0'}):
            code, elapsed, queries = self.detect(**kwargs)
            self.assertNotEqual(code, 0)
            self.assertLess(elapsed, 1.5)
            self.assertFalse(queries)

    def test_absent_input_is_bounded(self):
        code, elapsed, queries = self.detect(device=False)
        self.assertNotEqual(code, 0)
        self.assertLess(elapsed, 1.5)
        self.assertFalse(queries)

    def test_helper_and_command_paths_are_packaged(self):
        self.assertIn('scripts/gaoos-boot-mode ${INSTALL}', (BUSYBOX / 'package.mk').read_text())
        config = (BUSYBOX / 'config/busybox-init.conf').read_text()
        self.assertIn('CONFIG_TIMEOUT=y', config)
        self.assertIn('CONFIG_INSTALL_APPLET_SYMLINKS=y', config)
        self.assertIn('# CONFIG_INSTALL_APPLET_DONT is not set', config)

    def test_rgb30_driver_is_builtin_without_external_duplicate(self):
        kernel = (ROOT / 'projects/ROCKNIX/packages/linux/package.mk').read_text()
        self.assertIn('get_pkg_directory rocknix-joypad', kernel)
        self.assertIn('rocknix-singleadc-joypad.o', kernel)
        recipe = (ROOT / 'projects/ROCKNIX/packages/linux-drivers/rocknix-joypad/package.mk').read_text()
        self.assertIn('rocknix-joypad.ko', recipe)
        config = (ROOT / 'projects/ROCKNIX/devices/RK3566/linux/linux.aarch64.conf').read_text()
        for key in ('INPUT', 'INPUT_POLLDEV', 'INPUT_EVDEV', 'IIO', 'ROCKCHIP_SARADC',
                    'GPIO_ROCKCHIP', 'PINCTRL_ROCKCHIP', 'PWM_ROCKCHIP'):
            self.assertIn('CONFIG_' + key + '=y', config)

    def test_builtin_driver_is_not_packaged_again_as_a_module(self):
        recipe = ROOT / 'projects/ROCKNIX/packages/linux-drivers/rocknix-joypad/package.mk'
        for distro, device, expected in (
                ('GaoOS', 'RK3566', ['rocknix-joypad.ko']),
                ('ROCKNIX', 'RK3566', ['rocknix-joypad.ko', 'rocknix-singleadc-joypad.ko']),
                ('GaoOS', 'RK3588', ['rocknix-joypad.ko', 'rocknix-singleadc-joypad.ko'])):
            with self.subTest(distro=distro, device=device), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for name in ('rocknix-joypad.ko', 'rocknix-singleadc-joypad.ko'):
                    (root / name).write_text('module fixture')
                code = ('DISTRO=' + distro + '\nDEVICE=' + device + '\nINSTALL=' +
                        shlex.quote(str(root / 'install')) + '\n. ' + shlex.quote(str(recipe)) +
                        '\nget_full_module_dir() { echo usr/lib/modules/test; }\nmakeinstall_target\n')
                result = subprocess.run(['bash', '-s'], input=code, cwd=td,
                                        text=True, capture_output=True, timeout=3)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(sorted(p.name for p in (root / 'install').rglob('*.ko')), expected)

    def test_exact_key_query_hang_times_out(self):
        source = (BUSYBOX / 'scripts/init').read_text()
        start = source.index('is_key_pressed() {')
        function = source[start:source.index('\n}', start) + 2]
        with tempfile.TemporaryDirectory() as td:
            evtest = Path(td) / 'evtest'
            evtest.write_text('#!/bin/sh\nexec sleep 5\n')
            evtest.chmod(0o755)
            function = function.replace('/usr/bin/busybox timeout', 'timeout')
            function = function.replace('/usr/bin/evtest', shlex.quote(str(evtest)))
            start = time.monotonic()
            result = subprocess.run(['bash', '-s'], input=function + '\nis_key_pressed 315 /dev/null\n',
                                    text=True, capture_output=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            self.assertLess(time.monotonic() - start, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
