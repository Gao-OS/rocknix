#!/usr/bin/env python3
"""Scoped boot/build regressions; external disk/build commands are stubbed."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
PKG = ROOT / 'packages/gaoos/gaoos-ab-boot'


def shell(code, cwd=ROOT):
    return subprocess.run(['bash', '-s'], input=code, text=True, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def function(path, name):
    source = (ROOT / path).read_text()
    start = source.index(name + '() {')
    end = source.index('\n}', start) + 2
    return source[start:end]


class BootBuild(unittest.TestCase):
    def test_config_preserves_device_packages_and_adds_ab_after_overrides(self):
        source = (ROOT / 'config/options').read_text()
        source = source[:source.index('# use /bin/dash')]
        for distro, enabled in [('GaoOS', True), ('ROCKNIX', False)]:
            result = shell('''set -e
ROOT=$PWD DISTRO_DIR=$PWD/distributions PROJECT_DIR=$PWD/projects
DISTRO=%s PROJECT=ROCKNIX DEVICE=RK3566 TARGET_ARCH=aarch64
get_kernel_make_extracmd() { :; }
''' % distro + source[source.index('# read DISTRO options'):source.index('# read architecture defaults')]
                + source[source.index('# read local persistent options'):]
                + '\nprintf "PACKAGES=%s\\n" "$ADDITIONAL_PACKAGES"\n')
            self.assertEqual(result.returncode, 0, result.stdout)
            packages = result.stdout.split('PACKAGES=')[-1].split()
            self.assertIn('libmali', packages)
            self.assertEqual('gaoos-ab-boot' in packages, enabled, result.stdout)

    def test_boot_metadata_replaces_extlinux_only_for_ab(self):
        for enabled in ['yes', 'no']:
            for variant, fdt_type, fdt in [('Generic', 'FDTDIR', 'device_trees'),
                                           ('Specific', 'FDT', 'device_trees/rk3566-powkiddy-x55.dtb')]:
                with tempfile.TemporaryDirectory() as td:
                    result = shell('''set -e
IMG_TMP=%s RELEASE_DIR=%s/release GAOOS_AB_BOOT=%s SAVE_ERROR=%s/errors
DISTRO=GaoOS KERNEL_NAME=KERNEL DISTRO_BOOTLABEL=GAOOS DISTRO_DISKLABEL=STORAGE
EXTRA_CMDLINE="quiet console=ttyS2,1500000"
get_fdt_type() { echo %s; }
get_fdt() { echo %s; }
get_fdt_overlays() { :; }
mcopy() { :; }
''' % (td, td, enabled, td, fdt_type, fdt)
                        + function('projects/ROCKNIX/bootloader/mkimage', 'mkimage_extlinux')
                        + '\nmkimage_extlinux\n')
                    self.assertEqual(result.returncode, 0, result.stdout)
                    metadata = Path(td) / 'gaoos_boot.env'
                    extlinux = Path(td) / 'extlinux/extlinux.conf'
                    if enabled == 'yes':
                        self.assertTrue(metadata.exists(), result.stdout)
                        self.assertFalse(extlinux.exists())
                        data = metadata.read_text()
                        self.assertIn('extra_cmdline=quiet console=ttyS2,1500000', data)
                        self.assertIn('gaoos_fdt=' + ('' if variant == 'Generic' else fdt), data)
                        self.assertIn('gaoos_fdtdir=device_trees', data)
                    else:
                        self.assertTrue(extlinux.exists())
                        self.assertFalse(metadata.exists())

    def test_rk3566_enables_required_uboot_commands_only_for_ab(self):
        for variant in ['Generic', 'Specific']:
            for enabled in ['yes', 'no']:
                with tempfile.TemporaryDirectory() as td:
                    scripts = Path(td) / 'scripts'
                    scripts.mkdir()
                    (scripts / 'config').write_text('#!/bin/sh\nprintf \'CONFIG %s\\n\' \"$*\"\n')
                    (scripts / 'config').chmod(0o755)
                    result = shell('''set -e
PKG_BUILD=%s
GAOOS_AB_BOOT=%s
get_build_dir() { echo %s; }
setup_pkg_config_host() { :; }
find_file_path() { return 1; }
make() { printf 'MAKE %%s\\n' "$*"; }
''' % (td, enabled, td)
                        + '. projects/ROCKNIX/devices/RK3566/packages/u-boot-%s/package.mk\n' % variant
                        + 'pre_make_target\nmake_target || true\n')
                    self.assertEqual(result.returncode, 0, result.stdout)
                    for option in ['CMD_SETEXPR', 'CMD_FS_GENERIC', 'CMD_FAT', 'FAT_WRITE']:
                        self.assertEqual(option in result.stdout, enabled == 'yes', result.stdout)

    def test_boot_script_selects_slot_and_preserves_generic_or_specific_dtb(self):
        for active, fdt, board in [('a', '', 'rk3566-anbernic-rg353p.dtb'),
                                   ('a', '', 'rockchip/rk3566-anbernic-rg353p.dtb'),
                                   ('b', 'device_trees/rk3566-powkiddy-x55.dtb', 'rockchip/rk3566-quartz64-a.dtb')]:
            with tempfile.TemporaryDirectory() as td:
                state = Path(td) / 'ab_state.env'
                state.write_text('active_slot=%s\nstatus_a=good\nstatus_b=good\nretry_a=0\nretry_b=0\nmax_retries=3\n' % active)
                metadata = Path(td) / 'gaoos_boot.env'
                metadata.write_text('extra_cmdline=quiet console=ttyS2,1500000\ngaoos_fdt=%s\ngaoos_fdtdir=device_trees\ngaoos_overlays=\n' % fdt)
                (Path(td) / 'KERNEL').write_bytes(b'kernel')
                trees = Path(td) / 'device_trees'
                trees.mkdir()
                (trees / (fdt.split('/')[-1] if fdt else 'rk3566-anbernic-rg353p.dtb')).write_bytes(b'dtb')
                result = shell('''
devtype=mmc devnum=1 scriptaddr=1000 kernel_addr_r=2000 fdt_addr_r=3000 fdtoverlay_addr_r=4000
fdtfile=%s
setenv() { local name=$1; shift; printf -v "$name" '%%s' "$*"; }
load() { loaded=$4; printf 'LOAD %%s\\n' "$4"; [ -f '%s/'"$4" ]; }
env() {
 case "$1" in
 import) while IFS='=' read -r key value; do setenv "$key" "$value"; done < '%s/'"$loaded" ;;
 export) : ;;
 esac
}
save() { :; }
setexpr() { if [ "$2" = sub ]; then setenv "$1" "${5##*/}"; else setenv "$1" "$(( $2 + $4 ))"; fi; }
fdt() { :; }
sysboot() { echo 'LEGACY SYSBOOT'; }
booti() { printf 'BOOTARGS=%%s\\n' "$bootargs"; }
''' % (board, td, td) + (PKG / 'boot/boot.cmd').read_text())
                self.assertEqual(result.returncode, 0, result.stdout)
                self.assertIn('gaoos.slot=' + active, result.stdout)
                self.assertIn('gaoos.dev=mmcblk1', result.stdout)
                self.assertIn('quiet console=ttyS2,1500000', result.stdout)
                expected = fdt or 'device_trees/rk3566-anbernic-rg353p.dtb'
                self.assertIn('LOAD ' + expected, result.stdout)
                self.assertNotIn('LEGACY SYSBOOT', result.stdout)

    def test_init_uses_actual_flash_device_when_mmc_numbering_differs(self):
        result = shell("""
GAOOS_SLOT=b GAOOS_DEV=mmcblk1
progress() { :; }
awk() { echo /dev/mmcblk0p1; }
readlink() { echo /dev/mmcblk0p1; }
[() { if builtin [ "$1" = -b ]; then return 0; fi; if builtin [ "$1" = ! ] && builtin [ "$2" = -b ]; then return 1; fi; builtin [ "$@"; }
mount() { printf 'MOUNT %s\\n' "$*"; }
mount_part() { echo LEGACY; }
error() { echo ERROR; return 1; }
""" + function('projects/ROCKNIX/packages/sysutils/busybox/scripts/init', 'mount_sysroot')
            + '\nmount_sysroot\n')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('/dev/mmcblk0p3 /sysroot', result.stdout)
        self.assertNotIn('/dev/mmcblk1p3', result.stdout)

    def test_missing_boot_script_rejects_image_assembly(self):
        source = (ROOT / 'scripts/mkimage').read_text()
        start = source.index('  if [ "${GAOOS_AB_BOOT}" = "yes" ]; then', source.index('elif [ "${BOOTLOADER}" = "u-boot" ]'))
        end = source.index('\nelif [ "${BOOTLOADER}" = "arm-efi" ]', start)
        with tempfile.TemporaryDirectory() as td:
            result = shell('''RELEASE_DIR=%s IMG_TMP=%s GAOOS_AB_BOOT=yes SAVE_ERROR=%s/errors
mcopy() { :; }
show_error() { exit 1; }
''' % (td, td, td) + source[start:end])
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertNotIn('syntax error', result.stdout)

    def test_rk3566_uses_mkimage_from_its_real_uboot_package(self):
        result = shell("""
DEVICE=RK3566
. packages/gaoos/gaoos-ab-boot/package.mk
printf 'DEPS=%s\\n' "$PKG_DEPENDS_TARGET"
""")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('u-boot-Generic', result.stdout)
        self.assertNotIn('u-boot-tools:host', result.stdout)

    def test_package_orders_upstream_wrapper_install_before_ab_overrides(self):
        result = shell('DEVICE=RK3566\n. packages/gaoos/gaoos-ab-boot/package.mk\nprintf "%s\\n" "$PKG_DEPENDS_TARGET"\n')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('rocknix', result.stdout.split())

    def test_package_compiles_installs_and_enables_boot_services(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tools = td / 'uboot/tools'
            tools.mkdir(parents=True)
            tool = tools / 'mkimage'
            tool.write_text('#!/bin/sh\nfor output; do :; done\nprintf boot-script > "$output"\n')
            tool.chmod(0o755)
            (td / 'build').mkdir()
            result = shell("""set -e
DEVICE=RK3566 PKG_DIR=%s PKG_BUILD=%s/build INSTALL=%s/install TARGET_KERNEL_ARCH=arm64
get_build_dir() { echo %s/uboot; }
die() { echo "$*"; exit 1; }
""" % (PKG, td, td, td)
                + '. packages/gaoos/gaoos-ab-boot/package.mk\n'
                + function('config/functions', 'enable_service')
                + '\nmake_target\nmakeinstall_target\npost_install\n')
            self.assertEqual(result.returncode, 0, result.stdout)
            installed = td / 'install'
            self.assertEqual((installed / 'usr/share/bootloader/boot.scr').read_text(), 'boot-script')
            self.assertEqual((installed / 'usr/share/gaoos/ab_state.env.default').read_bytes(),
                             (PKG / 'boot/ab_state.env.default').read_bytes())
            for name in ['gaoos-boot-confirm', 'gaoos-watchdog']:
                self.assertTrue((installed / 'usr/bin' / name).stat().st_mode & 0o111)
                link = installed / 'usr/lib/systemd/system/multi-user.target.wants' / (name + '.service')
                self.assertTrue(link.is_symlink())
                self.assertTrue(link.exists())

    def test_boot_script_and_initial_state_are_copied_to_fat(self):
        source = (ROOT / 'scripts/mkimage').read_text()
        start = source.index('  if [ "${GAOOS_AB_BOOT}" = "yes" ]; then', source.index('elif [ "${BOOTLOADER}" = "u-boot" ]'))
        end = source.index('\nelif [ "${BOOTLOADER}" = "arm-efi" ]', start)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            loader = td / 'release/3rdparty/bootloader'
            loader.mkdir(parents=True)
            (loader / 'boot.scr').write_bytes(b'boot-script')
            (td / 'fat').mkdir()
            result = shell("""set -e
RELEASE_DIR=%s/release IMG_TMP=%s GAOOS_AB_BOOT=yes SAVE_ERROR=%s/errors FAT=%s/fat
mcopy() { local dest=${2#::}; cp "$1" "$FAT/${dest##*/}"; }
show_error() { exit 1; }
""" % (td, td, td, td) + source[start:end])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual((td / 'fat/boot.scr').read_bytes(), b'boot-script')
            self.assertIn('active_slot=a', (td / 'fat/ab_state.env').read_text())
            self.assertFalse((td / 'fat/SYSTEM').exists())

    def test_oversize_system_rejected_before_disk_assembly(self):
        source = (ROOT / 'scripts/mkimage').read_text()
        source = source[:source.index('# write a disklabel')]
        with tempfile.TemporaryDirectory() as td:
            result = shell("""TARGET_IMG=%s SYSTEM_SIZE=2048 SYSTEM_PART_START=32768
GAOOS_AB_BOOT=yes SLOT_SIZE=1 BOOT_SIZE=1 STORAGE_SIZE=32 BUILD_NAME=test IMAGE_NAME=test
stat() { echo 1048577; }
dd() { echo DISK_WRITE; }
sync() { :; }
""" % td + source)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn('exceeds slot size', result.stdout)
            self.assertNotIn('DISK_WRITE', result.stdout)

    def test_raw_ota_payload_and_checksum_are_retained(self):
        source = (ROOT / 'scripts/image').read_text()
        start = source.index('  mkdir -p ${RELEASE_DIR}/target')
        end = source.index('  # Create md5sum', start)
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / 'image.system').write_bytes(b'hsqs-test-system')
            (td / 'image.kernel').write_bytes(b'test-kernel')
            result = shell('set -e\nTARGET_IMG=%s IMAGE_NAME=image RELEASE_DIR=%s/release GAOOS_AB_BOOT=yes\n' % (td, td) + source[start:end])
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertTrue((td / 'image.squashfs').exists())
            result = subprocess.run(['sha256sum', '-c', 'image.squashfs.sha256'], cwd=td, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main(verbosity=2)
