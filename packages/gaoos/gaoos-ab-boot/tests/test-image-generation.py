#!/usr/bin/env python3
"""Failure-injection tests for image assembly, without privileged disk access.

Disk utilities are replaced only here to inject errors. Actual generated kernel,
initramfs, BusyBox and disk-image validation have separate integration checks.
"""
from pathlib import Path
import os
import shutil
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]


def function(name):
    source = (ROOT / 'projects/ROCKNIX/bootloader/mkimage').read_text()
    start = source.index(name + '() {')
    return source[start:source.index('\n}', start) + 2]


def shell(source):
    return subprocess.run(['bash', '-s'], input=source, cwd=ROOT, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


class ImageGeneration(unittest.TestCase):
    def test_real_config_source_accepts_no_subdevices_but_rejects_bad_xml(self):
        xmlstarlet = shutil.which('xmlstarlet')
        toolchain = ROOT / 'build.GaoOS-RK3566.aarch64/toolchain'
        if not xmlstarlet and (toolchain / 'bin/xmlstarlet').exists():
            xmlstarlet = str(toolchain / 'bin/xmlstarlet')
        if not xmlstarlet:
            self.skipTest('XMLStarlet required for real configuration integration')
        source = (ROOT / 'projects/ROCKNIX/bootloader/mkimage').read_text()
        config_source = source[:source.index('mkimage_uboot()')]
        actual_xml = (ROOT / 'projects/ROCKNIX/config.xml').read_text()
        for content in (actual_xml, '<rocknix><S922X>', None):
            with self.subTest(xml='valid' if content == actual_xml else 'invalid'):
                with tempfile.TemporaryDirectory() as td:
                    project = Path(td) / 'ROCKNIX'
                    project.mkdir()
                    if content is not None:
                        (project / 'config.xml').write_text(content)
                    env = os.environ.copy()
                    env['PATH'] = str(Path(xmlstarlet).parent) + ':' + env['PATH']
                    if Path(xmlstarlet).is_relative_to(toolchain):
                        env['LD_LIBRARY_PATH'] = str(toolchain / 'lib') + ':' + env.get('LD_LIBRARY_PATH', '')
                    code = '''
set -Ee -o pipefail
DISTRO=GaoOS PROJECT=ROCKNIX DEVICE=S922X GAOOS_AB_BOOT=yes
PROJECT_DIR=%s SAVE_ERROR=%s/errors
show_error() { cat "$SAVE_ERROR"; exit 1; }
''' % (shlex.quote(td), shlex.quote(td)) + config_source + '''
[ -z "$SUBDEVICES" ] || exit 2
set -o | sed -n '/pipefail[[:space:]]*on/p'
echo CONFIG_LOADED
'''
                    result = subprocess.run(['bash', '-s'], input=code, cwd=ROOT, env=env,
                                            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                    if content == actual_xml:
                        self.assertEqual(result.returncode, 0, result.stdout)
                        self.assertIn('CONFIG_LOADED', result.stdout)
                        self.assertRegex(result.stdout, r'pipefail\s+on')
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertNotIn('CONFIG_LOADED', result.stdout)

    def assembly(self, failure='', system=b'hsqs' + bytes(128), extra=''):
        with tempfile.TemporaryDirectory() as directory:
            td = Path(directory)
            loader = td / 'release/3rdparty/bootloader'
            loader.mkdir(parents=True)
            (loader / 'Generic_uboot.bin').write_bytes(b'uboot')
            (loader / 'boot.scr').write_bytes(b'script')
            trees = loader / 'device_trees'
            trees.mkdir()
            (trees / 'rk3566-powkiddy-rgb30.dtb').write_bytes(b'dtb')
            (td / 'image.system').write_bytes(system)
            (td / 'image.kernel').write_bytes(b'kernel')
            target = td / 'release/target'
            target.mkdir()
            (target / 'KERNEL.md5').write_text('checksum')
            hooks = td / 'hooks/ROCKNIX/bootloader'
            hooks.mkdir(parents=True)
            # Bootloader helper fault injection is tested separately below.
            (hooks / 'mkimage').write_text(':\n')
            result = shell('''
exec 3>&1
TARGET_IMG="%s" RELEASE_DIR="%s/release" ROOT="$PWD" SCRIPTS="$PWD/scripts"
SYSTEM_SIZE=64 SYSTEM_PART_START=32768 GAOOS_AB_BOOT=yes
BOOT_SIZE=32 SLOT_SIZE=1 STORAGE_SIZE=32 BUILD_NAME=image IMAGE_NAME=image
DEVICE=RK3566 SUBDEVICE=Generic PROJECT=ROCKNIX BOOTLOADER=u-boot
PARTITION_TABLE=gpt KERNEL_NAME=KERNEL DISTRO_BOOTLABEL=GAOOS
PROJECT_DIR="%s/hooks" UUID_SYSTEM=1234-5678 UUID_STORAGE=test
FAIL_COMMAND="%s"
dd() { echo DD >&3; }
sync() { :; }
parted() {
  echo "PARTED $*"
  if [[ "$FAIL_COMMAND" = partition && " $* " = *" mkpart "* ]]; then return 1; fi
  [ "$FAIL_COMMAND" != parted ]
}
mke2fs() { echo MKE2FS; [ "$FAIL_COMMAND" != mke2fs ]; }
tune2fs() { echo TUNE2FS; }
e2fsck() { :; }
populatefs() { :; }
mformat() { echo "MFORMAT $*" >&3; }
mcopy() { echo MCOPY; }
fsck.fat() { :; }
pigz() { echo COMPRESS; }
sha256sum() { :; }
# Structural input validation is covered by test-validate-boot.py. This harness
# isolates assembly failures and does not pretend its synthetic inputs boot.
python3() { echo VALIDATE >&3; }
unsquashfs() { [ "$FAIL_COMMAND" != unsquashfs ]; }
%s
''' % (td, td, td, failure, extra) + (ROOT / 'scripts/mkimage').read_text())
            return result

    def test_valid_assembly_reaches_compression(self):
        result = self.assembly()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('MKE2FS', result.stdout)
        self.assertIn('DD', result.stdout)
        self.assertIn('COMPRESS', result.stdout)
        self.assertIn('VALIDATE', result.stdout)

    def test_gaoos_requires_fat32_and_preserves_upstream_formatter(self):
        for enabled in ('yes', 'no'):
            with self.subTest(ab=enabled):
                result = self.assembly(extra='GAOOS_AB_BOOT=' + enabled)
                self.assertEqual(result.returncode, 0, result.stdout)
                line = next(line for line in result.stdout.splitlines() if line.startswith('MFORMAT '))
                self.assertEqual('-F' in line.split(), enabled == 'yes', line)

    def test_other_devices_keep_general_checks_without_rk3566_validator(self):
        result = self.assembly(extra='DEVICE=S922X')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('VALIDATE', result.stdout)
        result = self.assembly(failure='unsquashfs', extra='DEVICE=S922X')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('DD', result.stdout)

    def test_release_kernel_validator_is_rk3566_ab_only(self):
        source = (ROOT / 'scripts/image').read_text()
        start = source.index('if [ "${GAOOS_AB_BOOT}"', source.index('# Copy kernel to target dir'))
        block = source[start:source.index('# Set mksquashfs options', start)]
        for device, enabled in [('RK3566', 'yes'), ('S922X', 'yes'), ('RK3566', 'no')]:
            with self.subTest(device=device, enabled=enabled):
                result = shell('DEVICE=%s GAOOS_AB_BOOT=%s\npython3() { echo VALIDATE; }\n' %
                               (device, enabled) + block)
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertEqual('VALIDATE' in result.stdout, device == 'RK3566' and enabled == 'yes')

    def test_missing_boot_files_are_rejected_before_disk_write(self):
        for path in ['Generic_uboot.bin', 'boot.scr',
                     'device_trees/rk3566-powkiddy-rgb30.dtb']:
            with self.subTest(path=path):
                result = self.assembly(extra='rm "$RELEASE_DIR/3rdparty/bootloader/%s"' % path)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('DD', result.stdout)

    def test_partition_failure_stops_before_formatting(self):
        for failure in ['parted', 'partition']:
            with self.subTest(failure=failure):
                result = self.assembly(failure=failure)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertNotIn('MKE2FS', result.stdout)
                self.assertNotIn('COMPRESS', result.stdout)

    def test_filesystem_creation_failure_stops_before_tuning(self):
        result = self.assembly(failure='mke2fs')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('TUNE2FS', result.stdout)
        self.assertNotIn('COMPRESS', result.stdout)

    def test_corrupt_squashfs_is_rejected_before_disk_write(self):
        result = self.assembly(failure='unsquashfs')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('DD', result.stdout)

    def test_empty_payload_is_rejected_before_disk_write(self):
        result = self.assembly(system=b'')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('DD', result.stdout)

    def test_invalid_partition_size_is_rejected_before_disk_write(self):
        result = self.assembly(extra='SLOT_SIZE=0')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('DD', result.stdout)

    def test_rk3566_rejects_non_gpt_layout(self):
        result = self.assembly(extra='PARTITION_TABLE=msdos')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('DD', result.stdout)

    def test_missing_uboot_fails(self):
        with tempfile.TemporaryDirectory() as td:
            result = shell('''
RELEASE_DIR="%s" SUBDEVICE=Generic GAOOS_AB_BOOT=yes DEVICE=RK3566
show_error() { exit 1; }
''' % td + function('mkimage_uboot') + '\nmkimage_uboot\n')
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_empty_generic_uboot_cannot_fall_back_to_other_board(self):
        with tempfile.TemporaryDirectory() as td:
            loader = Path(td) / '3rdparty/bootloader'
            loader.mkdir(parents=True)
            (loader / 'Generic_uboot.bin').touch()
            (loader / 'uboot.bin').write_bytes(b'other-board')
            result = shell('''
RELEASE_DIR="%s" SUBDEVICE=Generic GAOOS_AB_BOOT=yes DEVICE=RK3566
SAVE_ERROR="%s/error" DISK="%s/disk"
show_error() { exit 1; }
dd() { echo WRONG_BOARD_WRITE; }
''' % (td, td, td) + function('mkimage_uboot') + '\nmkimage_uboot\n')
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_dtb_copy_failure_fails(self):
        result = shell('''
GAOOS_AB_BOOT=yes DEVICE=RK3566 RELEASE_DIR=/missing SAVE_ERROR=/dev/null
show_error() { exit 1; }
mcopy() { return 1; }
''' + function('mkimage_dtb') + '\nmkimage_dtb\n')
        self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_missing_rgb30_dtb_rejected_for_generic(self):
        with tempfile.TemporaryDirectory() as td:
            result = shell('''
GAOOS_AB_BOOT=yes DEVICE=RK3566 SUBDEVICE=Generic RELEASE_DIR="%s"
show_error() { exit 1; }
mcopy() { :; }
''' % td + function('mkimage_dtb') + '\nmkimage_dtb\n')
            self.assertNotEqual(result.returncode, 0, result.stdout)

    def test_squashfs_host_package_builds_and_installs_inspector(self):
        result = shell('''
get_pkg_directory() { :; }
make() { echo "MAKE $*"; }
mkdir() { :; }
cp() { echo "COPY $*"; }
TOOLCHAIN=/toolchain
. projects/ROCKNIX/packages/sysutils/squashfs-tools/package.mk
make_host
makeinstall_host
''')
        self.assertEqual(result.returncode, 0, result.stdout)
        make_line = next(line for line in result.stdout.splitlines() if line.startswith('MAKE '))
        copy_line = next(line for line in result.stdout.splitlines() if line.startswith('COPY '))
        self.assertIn('unsquashfs', make_line.split())
        self.assertIn('squashfs-tools/unsquashfs', copy_line.split())

    def test_boot_env_does_not_ignore_failed_fdt_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            result = shell('''
GAOOS_AB_BOOT=yes IMG_TMP="%s" RELEASE_DIR="%s" SAVE_ERROR="%s/errors"
show_error() { exit 1; }
get_fdt_type() { echo FDTDIR; }
get_fdt() { return 1; }
get_fdt_overlays() { :; }
mcopy() { :; }
''' % (td, td, td) + function('mkimage_extlinux') + '\nmkimage_extlinux\n')
            self.assertNotEqual(result.returncode, 0, result.stdout)


if __name__ == '__main__':
    unittest.main()
