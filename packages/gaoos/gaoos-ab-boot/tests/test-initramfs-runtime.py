#!/usr/bin/env python3
"""Validate actual packaged AArch64 initramfs commands under QEMU user mode.

This exercises the packaged BusyBox ash/applications, not a host command shim.
It checks the initramfs directory generated before kernel embedding; use the
image validator separately to compare the embedded archive with this directory.
"""
import argparse
from pathlib import Path
import shlex
import subprocess
import tempfile

# RK3566 local MMC boot, diagnostics, recovery, and legacy update dependencies.
# echo/test/read/printf/kill are ash builtins. iscsistart and nbd-client belong to
# optional network boot configurations, which this RK3566 validation excludes.
APPLETS = '''awk basename cat chroot clear cp cut dd df dmesg ether-wake fbset
    grep gunzip head insmod ln losetup ls md5sum mkdir mkfifo mount mountpoint mv
    ping poweroff readlink reboot rm sed setfont setsid sh sleep sort stat stty
    switch_root sync tail tar tee tr umount uname usleep cttyhack'''.split()
EXTERNALS = ('usr/sbin/fsck', 'usr/sbin/fsck.ext4', 'usr/sbin/fsck.vfat',
             'usr/sbin/blkid', 'usr/bin/avfsd', 'usr/bin/rocknix-splash',
             'usr/bin/evtest')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('initramfs', type=Path)
    parser.add_argument('--qemu', required=True, type=Path,
                        help='qemu-aarch64 or qemu-aarch64-static executable')
    args = parser.parse_args()
    root = args.initramfs.resolve()
    binary = root / 'usr/bin/busybox'
    assert binary.is_file(), f'Missing BusyBox: {binary}'
    for relative in EXTERNALS:
        assert (root / relative).is_file(), f'Missing initramfs dependency: {relative}'
    config = (Path(__file__).resolve().parents[4] /
              'projects/ROCKNIX/packages/sysutils/busybox/config/busybox-init.conf').read_text()
    for option in ('CONFIG_FEATURE_SH_STANDALONE=y', 'CONFIG_FEATURE_PREFER_APPLETS=y'):
        assert option in config, f'Commands without symlinks require {option}'

    # The kernel's initramfs.conf supplies these top-level symlinks. Mirror them
    # for the QEMU loader without altering the build's initramfs staging tree.
    with tempfile.TemporaryDirectory(prefix='gaoos-initramfs-runtime-') as td:
        work = Path(td)
        (work / 'usr').symlink_to(root / 'usr')
        (work / 'lib').symlink_to(root / 'usr/lib')
        qemu = [str(args.qemu.resolve()), '-0', 'busybox', '-L', td, str(binary)]

        def invoke(*argv):
            result = subprocess.run(qemu + list(argv), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=15)
            assert result.returncode == 0, f'{argv}: {result.stdout}'
            return result.stdout

        applets = set(invoke('--list').split())
        missing = sorted(set(APPLETS) - applets)
        assert not missing, f'Missing BusyBox applets: {missing}'
        assert invoke('readlink', '-f', '/dev/null').strip() == '/dev/null'
        # The init script invokes unqualified commands via ash standalone mode.
        assert invoke('sh', '-c', 'PATH=/nonexistent; readlink -f /dev/null').strip() == '/dev/null'
        # Exercise the actual shell/FIFO/tee path, including final error flushing.
        source = (root / 'init').read_text()

        def function(name):
            start = source.index(name + '() {')
            return source[start:source.index('\n}', start) + 2]

        # Standalone ash re-execs /proc/self/exe for forked applets. In QEMU
        # user mode without binfmt registration that path is the emulator, not
        # the target program. Re-enter the same emulator/binary explicitly for
        # these applets; this is an execution adapter, never a host utility or
        # a command mock. Native hardware uses normal standalone dispatch.
        adapters = '\n'.join(name + '() { ' + shlex.join(qemu + [name]) + ' "$@"; }'
                             for name in ('mkfifo', 'tee', 'rm'))
        program = (adapters + '\n' + function('start_boot_logging').replace('/run/', td + '/') + '\n' +
                   function('stop_boot_logging') + '\nPATH=/nonexistent\nTEE_PID=""\n' +
                   'start_boot_logging\nexec 3>&1\necho "runtime stdout"\necho "runtime stderr" >&2\n' +
                   'stop_boot_logging ' + shlex.quote(str(work / 'console')) + '\n')
        output = invoke('sh', '-c', program)
        assert 'runtime stderr' in output, output
        assert (work / 'initramfs.log').read_text() == 'runtime stdout\nruntime stderr\n'
        assert not (work / 'initramfs.pipe').exists()
        # This is safe on a nonexistent device and proves evtest loads, rather
        # than assuming an ELF file's presence means its runtime is available.
        result = subprocess.run([str(args.qemu.resolve()), '-L', td,
                                 str(root / 'usr/bin/evtest'), '--query',
                                 '/nonexistent-gaoos-input', 'EV_KEY', '114'],
                                text=True, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=5)
        assert result.returncode == 1 and 'No such file' in result.stdout, result.stdout
        print(f'PASS: {len(APPLETS)} packaged BusyBox applets; readlink -f; ash standalone; '
              f'FIFO stderr/log drain (explicit QEMU re-exec adapter); '
              f'{len(EXTERNALS)} packaged executables; evtest runtime')


if __name__ == '__main__':
    main()
