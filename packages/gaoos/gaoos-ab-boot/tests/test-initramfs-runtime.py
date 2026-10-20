#!/usr/bin/env python3
"""Validate real packaged AArch64 initramfs commands and logging under QEMU.

Accepts the staging directory or an extracted kernel archive. The image validator
must separately prove that this tree is the archive embedded in the shipped FIT.
QEMU's re-exec adapter invokes only the packaged target BusyBox/executables.
"""
import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time

# RK3566 MMC boot, diagnostics, recovery and legacy update command dependencies.
# echo/test/read/printf/kill are ash builtins; optional iSCSI/NBD boot is excluded.
APPLETS = '''awk basename cat chroot clear cp cut dd df dmesg ether-wake fbset
    grep gunzip head insmod ln losetup ls md5sum mkdir mkfifo mount mountpoint mv
    ping poweroff readlink reboot rm sed setfont setsid sh sleep sort stat stty
    switch_root sync tail tar tee timeout tr umount uname usleep cttyhack'''.split()
EXTERNALS = ('usr/sbin/fsck', 'usr/sbin/fsck.ext4', 'usr/sbin/fsck.vfat',
             'usr/sbin/blkid', 'usr/bin/avfsd', 'usr/bin/gaoos-splash',
             'usr/bin/evtest')


def target_path(root, relative):
    """Resolve target-absolute symlinks within the archive, never on the host."""
    parts = list(Path(relative).parts)
    resolved = []
    links = 0
    while parts:
        item = parts.pop(0)
        if item in ('/', '.'):
            continue
        if item == '..':
            assert resolved, f'Path escapes initramfs: {relative}'
            resolved.pop()
            continue
        candidate = root.joinpath(*resolved, item)
        if candidate.is_symlink():
            links += 1
            assert links <= 40, f'Symlink loop: {relative}'
            link = Path(os.readlink(candidate))
            if link.is_absolute():
                resolved = []
            parts = list(link.parts) + parts
        else:
            resolved.append(item)
    return root.joinpath(*resolved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('initramfs', type=Path)
    parser.add_argument('--qemu', required=True, type=Path,
                        help='qemu-aarch64 or qemu-aarch64-static executable')
    args = parser.parse_args()
    root = args.initramfs.resolve()
    binary = root / 'usr/bin/busybox'
    assert binary.is_file(), f'Missing BusyBox: {binary}'

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
        # Applet support alone did not catch the missing mountpoint pathname.
        # Require real installed links, including paths used by external scripts.
        for name in APPLETS:
            paths = [root / 'usr' / directory / name for directory in ('bin', 'sbin')]
            installed = [p for p in paths if p.is_symlink() and
                         target_path(root, p.relative_to(root)) == binary]
            assert installed, f'Missing packaged command symlink: {name}'
            assert invoke('sh', '-c', f'PATH=/nonexistent; type {shlex.quote(name)}').strip(), name
        for relative in EXTERNALS:
            executable = target_path(root, relative)
            assert executable.is_file() and executable.stat().st_mode & 0o111, \
                f'Missing executable initramfs dependency: {relative}'
        helper = root / 'gaoos-boot-mode'
        assert helper.is_file(), 'Missing packaged /gaoos-boot-mode'
        invoke('sh', '-n', str(helper))
        assert invoke('readlink', '-f', '/dev/null').strip() == '/dev/null'
        # Execute via the actual installed link and argv[0], not only busybox APPLET.
        mountpoint = next(p for p in (root / 'usr/bin/mountpoint', root / 'usr/sbin/mountpoint')
                          if p.is_symlink())
        result = subprocess.run([str(args.qemu.resolve()), '-0', 'mountpoint', '-L', td,
                                 str(target_path(root, mountpoint.relative_to(root))), '-q', '/proc'],
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr

        source = (root / 'init').read_text()

        def function(name):
            start = source.index(name + '() {')
            return source[start:source.index('\n}', start) + 2]

        # Without binfmt, standalone ash's /proc/self/exe points at the emulator.
        # Route re-execs back through QEMU. Lookup/link checks above remain real.
        adapters = '\n'.join(name.replace('-', '_') + '() { ' +
                             shlex.join(qemu + [name]) + ' "$@"; }'
                             for name in ('mkfifo', 'tee', 'rm', 'cat', 'clear'))
        adapters += '\nbb() { ' + shlex.join(qemu) + ' "$@"; }\n'
        funcs = '\n'.join(function(n) for n in
                          ('start_boot_logging', 'stop_boot_logging', 'enable_boot_console'))
        funcs = funcs.replace('/run/', td + '/').replace('/dev/console', str(work / 'console'))
        funcs = funcs.replace('/usr/bin/busybox', 'bb')
        for verbose in (False, True):
            log = work / 'initramfs.log'
            console = work / 'console'
            log.unlink(missing_ok=True)
            console.unlink(missing_ok=True)
            program = adapters + '\n' + funcs + '''
PATH=/nonexistent
TEE_PID="" BOOT_CONSOLE=no
start_boot_logging
echo 'buffered stdout'
echo 'buffered stderr' >&2
'''
            if verbose:
                program += 'enable_boot_console\n'
            program += "echo 'late stdout'\necho 'late stderr' >&2\n"
            program += 'stop_boot_logging ' + shlex.quote(str(console)) + '\n'
            invoke('sh', '-c', program)
            expected = 'buffered stdout\nbuffered stderr\nlate stdout\nlate stderr\n'
            assert log.read_text() == expected, log.read_text()
            actual_console = console.read_text() if console.exists() else ''
            if verbose:
                assert expected in actual_console, actual_console
                assert actual_console.count('buffered stdout') == 1, actual_console
            else:
                assert actual_console == '', actual_console
            assert not (work / 'initramfs.pipe').exists()

        # Prove timeout can terminate a hung target command with the built applet.
        sleep = qemu.copy()
        sleep[2] = 'sleep'
        start = time.monotonic()
        result = subprocess.run(qemu + ['timeout', '-s', 'KILL', '1'] + sleep + ['5'],
                                capture_output=True, text=True, timeout=3)
        assert result.returncode != 0 and time.monotonic() - start < 2, result
        # EVIOCGKEY on an invalid device is safe; failure must load all ELF deps.
        evtest = [str(args.qemu.resolve()), '-L', td, str(root / 'usr/bin/evtest')]
        result = subprocess.run(evtest + ['--query', '/nonexistent-gaoos-input', 'EV_KEY', '315'],
                                text=True, capture_output=True, timeout=3)
        assert result.returncode == 1 and 'No such file' in result.stderr + result.stdout, result
        key_function = function('is_key_pressed').replace('/usr/bin/busybox', 'bb')
        key_function = key_function.replace('/usr/bin/evtest', shlex.join(evtest))
        result = subprocess.run(qemu + ['sh', '-c', adapters + '\n' + key_function +
                                '\nis_key_pressed 315 /dev/null\n'],
                                text=True, capture_output=True, timeout=3)
        assert result.returncode == 1 and 'ioctl' in result.stdout + result.stderr, result
        # Splash must resolve its actual dependencies even without framebuffer.
        assert not Path('/dev/fb0').exists(), 'Run this no-framebuffer smoke on a host without /dev/fb0'
        result = subprocess.run([str(args.qemu.resolve()), '-L', td,
                                 str(root / 'usr/bin/gaoos-splash')],
                                text=True, capture_output=True, timeout=3)
        assert result.returncode == 1 and 'No such file' in result.stdout + result.stderr, result
        print(f'PASS: {len(APPLETS)} real packaged applets/command symlinks/ash lookup; '
              'mountpoint link execution; quiet RAM log; verbose replay/FIFO drain; '
              'one-second timeout; START evtest failure path; packaged helper syntax; '
              f'{len(EXTERNALS)} packaged executables; splash no-framebuffer runtime')


if __name__ == '__main__':
    main()
