#!/usr/bin/env python3
"""Validate RK3566 boot artifacts, including the initramfs inside KERNEL.

Build-time checks use the compiled BusyBox applet-name table, never host applets.
--qemu additionally executes the extracted target binary and shell at runtime.
"""
import argparse
import hashlib
import json
import os
import posixpath
from pathlib import Path
import re
import stat
import struct
import subprocess
import tempfile
import zlib

REQUIRED_APPLETS = {
    'ash', 'awk', 'cat', 'clear', 'cp', 'cttyhack', 'cut', 'dd', 'df', 'dmesg',
    'grep', 'head', 'kill', 'ln', 'losetup', 'ls', 'md5sum', 'mkdir', 'mkfifo',
    'mknod', 'modprobe', 'mount', 'mountpoint', 'mv', 'poweroff', 'readlink',
    'reboot', 'rm', 'sed', 'setsid', 'sh', 'sleep', 'switch_root', 'sync',
    'tee', 'timeout', 'tr', 'umount', 'usleep',
}
LZO_MAGIC = b'\x89LZO\x00\r\n\x1a\n'
REQUIRED_EXTERNALS = ('usr/sbin/fsck', 'usr/sbin/fsck.ext4', 'usr/sbin/fsck.vfat',
                      'usr/sbin/blkid', 'usr/bin/avfsd', 'usr/bin/gaoos-splash',
                      'usr/bin/evtest')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def parse_cpio(data):
    entries, offset = {}, 0
    while offset + 110 <= len(data):
        require(data[offset:offset + 6] in (b'070701', b'070702'), 'Invalid initramfs cpio header')
        fields = [int(data[offset + i:offset + i + 8], 16) for i in range(6, 110, 8)]
        mode, size, namesize = fields[1], fields[6], fields[11]
        start = offset + 110
        require(namesize > 0 and start + namesize <= len(data), 'Truncated initramfs name')
        require(data[start + namesize - 1] == 0, 'Invalid initramfs filename')
        name = data[start:start + namesize - 1].decode().lstrip('./')
        start = (start + namesize + 3) & ~3
        require(start + size <= len(data), 'Truncated initramfs payload')
        if name == 'TRAILER!!!':
            return entries
        require('..' not in Path(name).parts, 'Unsafe initramfs path')
        content = data[start:start + size]
        if stat.S_ISLNK(mode):
            # Linux gen_init_cpio includes the target's terminating NUL.
            content = content.rstrip(b'\0')
            require(content and b'\0' not in content, 'Invalid initramfs symlink')
        entries[name] = (mode, content)
        offset = (start + size + 3) & ~3
    raise ValueError('Truncated initramfs archive (no trailer)')


def lzo_stream(data):
    """Bound one lzop stream so trailing kernel data cannot hide corruption."""
    require(data.startswith(LZO_MAGIC) and len(data) > 38, 'Invalid LZO initramfs')
    version = struct.unpack_from('>H', data, 9)[0]
    require(version >= 0x0940, 'Unsupported old lzop stream')
    flags = struct.unpack_from('>I', data, 17)[0]
    offset = 21 + (4 if flags & 0x800 else 0) + 12
    offset += 1 + data[offset] + 4
    if flags & 0x40:
        length = struct.unpack_from('>I', data, offset)[0]
        offset += 4 + length + 4
    while True:
        size = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        if size == 0:
            return data[:offset]
        compressed = struct.unpack_from('>I', data, offset)[0]
        offset += 4
        require(0 < compressed <= size <= 64 * 1024 * 1024, 'Invalid LZO block size')
        offset += 4 * bool(flags & 1) + 4 * bool(flags & 0x100)
        if compressed < size:
            offset += 4 * bool(flags & 2) + 4 * bool(flags & 0x200)
        offset += compressed
        require(offset <= len(data), 'Truncated LZO initramfs')


def embedded_cpio(image):
    offset = image.find(LZO_MAGIC)
    while offset >= 0:
        # The decompressor's own magic constant also occurs in kernel rodata.
        try:
            stream = lzo_stream(image[offset:])
        except (ValueError, IndexError, struct.error):
            offset = image.find(LZO_MAGIC, offset + 1)
            continue
        result = subprocess.run(['lzop', '-d', '-c'], input=stream, capture_output=True)
        require(result.returncode == 0, 'Cannot decompress embedded LZO initramfs: ' + result.stderr.decode())
        return parse_cpio(result.stdout)
    # Support uncompressed and gzip kernels for explicit recovery builds too.
    for magic in (b'070701', b'070702'):
        offset = image.find(magic)
        while offset >= 0:
            try:
                entries = parse_cpio(image[offset:])
                if 'init' in entries:
                    return entries
            except (ValueError, UnicodeDecodeError):
                pass
            offset = image.find(magic, offset + 1)
    offset = image.find(b'\x1f\x8b\x08')
    if offset >= 0:
        raw = zlib.decompress(image[offset:], 16 + zlib.MAX_WBITS)
        return parse_cpio(raw)
    raise ValueError('KERNEL has no supported embedded initramfs')


def applet_names(binary):
    # Unlike ELF imports or usage strings, this sorted NUL-separated table lists
    # compiled applets. readlink(2) may be imported even with CONFIG_READLINK=n.
    start = binary.find(b'ash\0awk\0')
    require(start >= 0, 'BusyBox compiled applet table missing')
    names = []
    for item in binary[start:].split(b'\0'):
        if not re.fullmatch(rb'[a-z][a-z0-9_.+-]*', item):
            break
        name = item.decode()
        if names and name <= names[-1]:
            break
        names.append(name)
    return set(names)


def validate_kernel(image, expected_init=None, expected_busybox=None):
    require(len(image) >= 64 and image[56:60] == b'ARM\x64', 'Invalid AArch64 KERNEL Image header')
    image_size = struct.unpack_from('<Q', image, 16)[0]
    require(image_size > 0 and len(image) <= image_size, 'Invalid AArch64 KERNEL image size')
    entries = embedded_cpio(image)
    for path in ('init', 'usr/bin/busybox'):
        require(path in entries and stat.S_ISREG(entries[path][0]) and entries[path][0] & 0o111,
                'Embedded executable missing: ' + path)
    init, busybox = entries['init'][1], entries['usr/bin/busybox'][1]
    require(init.startswith(b'#!/bin/sh'), 'Embedded init has invalid interpreter')
    require(busybox.startswith(b'\x7fELF'), 'Embedded BusyBox is not ELF')
    require(len(busybox) > 20 and busybox[4] == 2 and struct.unpack_from('<H', busybox, 18)[0] == 183,
            'Embedded BusyBox is not AArch64 ELF64')
    for external in REQUIRED_EXTERNALS:
        path = external
        for _ in range(16):
            require(path in entries, 'Missing embedded executable: ' + external)
            mode, content = entries[path]
            if not stat.S_ISLNK(mode):
                require(stat.S_ISREG(mode) and mode & 0o111, 'Invalid embedded executable: ' + external)
                break
            target = content.decode()
            path = posixpath.normpath(posixpath.join(posixpath.dirname(path), target)) if not target.startswith('/') else target.lstrip('/')
        else:
            raise ValueError('Unresolvable embedded executable: ' + external)
    missing = REQUIRED_APPLETS - applet_names(busybox)
    require(not missing, 'Embedded BusyBox missing applets: ' + ', '.join(sorted(missing)))
    if expected_init is not None:
        require(init == expected_init, 'Embedded init differs from staged init')
    if expected_busybox is not None:
        require(busybox == expected_busybox, 'Embedded BusyBox differs from staged BusyBox')
    return entries


def validate_quiet_boot(entries, staged=None):
    helper = entries.get('gaoos-boot-mode')
    require(helper is not None and stat.S_ISREG(helper[0]) and
            b'detect_gaoos_start()' in helper[1], 'Missing embedded gaoos-boot-mode helper')
    require('usr/bin/rocknix-splash' not in entries, 'ROCKNIX splash present in GaoOS initramfs')
    for name in ('mountpoint', 'timeout'):
        link = entries.get('usr/bin/' + name)
        require(link is not None and stat.S_ISLNK(link[0]) and link[1] == b'busybox',
                'Missing embedded BusyBox applet link: ' + name)
    if staged is not None:
        for name in ('init', 'gaoos-boot-mode', 'usr/bin/busybox', 'usr/bin/evtest', 'usr/bin/gaoos-splash'):
            require(entries[name][1] == (staged / name).read_bytes(),
                    'Embedded quiet-boot dependency differs from staging: ' + name)


def validate_script(data):
    require(len(data) >= 72, 'Truncated U-Boot boot.scr')
    magic, crc, _, size, _, _, payload_crc = struct.unpack_from('>7I', data)
    require(magic == 0x27051956 and data[30] == 6 and data[31] == 0, 'Invalid U-Boot script header')
    require(data[29] == 22, 'boot.scr is not an ARM64 U-Boot script')
    header = bytearray(data[:64])
    header[4:8] = b'\0' * 4
    require(zlib.crc32(header) == crc, 'boot.scr header CRC mismatch')
    require(len(data) == 64 + size and zlib.crc32(data[64:]) == payload_crc, 'boot.scr data CRC mismatch')


def runtime(entries, qemu):
    with tempfile.TemporaryDirectory(prefix='gaoos-initramfs-') as td:
        root = Path(td)
        # Extract regular files only. QEMU's -L maps the target ELF loader/libs;
        # target execution does not need host root mounts or privileged chroot.
        for name, (mode, data) in entries.items():
            if stat.S_ISREG(mode):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(mode & 0o777)
        for name, (mode, data) in entries.items():
            if stat.S_ISLNK(mode):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                target = data.decode()
                if target.startswith('/'):
                    target = os.path.relpath(root / target.lstrip('/'), path.parent)
                path.symlink_to(target)
        binary = str(root / 'usr/bin/busybox')
        command = [qemu, '-0', 'busybox', '-L', td, binary]
        names = subprocess.check_output(command + ['--list'], text=True).splitlines()
        require(REQUIRED_APPLETS <= set(names), 'Runtime BusyBox applet list incomplete')
        resolved = subprocess.check_output(command + ['readlink', '-f', '/dev/null'], text=True)
        require(resolved.strip() == '/dev/null', 'BusyBox readlink -f failed')
        # This initramfs ash does not enable the optional `command` builtin.
        # Applet enumeration above verifies mount/mkfifo; keep readlink last so
        # QEMU needs no unsupported /proc/self/exe re-execution between commands.
        resolved = subprocess.check_output(command + ['sh', '-c',
            'PATH=/unavailable; readlink -f /dev/null'], text=True)
        require(resolved.strip() == '/dev/null', 'Standalone BusyBox readlink -f failed')
        return {'applets': len(names), 'readlink_follow': 'PASS', 'standalone_shell': 'PASS'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--init', type=Path)
    parser.add_argument('--busybox', type=Path)
    parser.add_argument('--quiet-boot', action='store_true', help='Require GaoOS boot helper and actual applet symlinks')
    parser.add_argument('--initramfs', type=Path, help='Compare embedded quiet-boot dependencies with this staging directory')
    parser.add_argument('--boot-script', type=Path)
    parser.add_argument('--qemu', help='Path to qemu-aarch64 user-mode emulator')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    require(args.kernel or args.boot_script, 'Specify --kernel or --boot-script')
    report = {'status': 'PASS', 'hardware': 'HARDWARE_BOOT_UNVERIFIED'}
    if args.kernel:
        image = args.kernel.read_bytes()
        entries = validate_kernel(image, args.init.read_bytes() if args.init else None,
                                  args.busybox.read_bytes() if args.busybox else None)
        report.update(kernel_sha256=hashlib.sha256(image).hexdigest(),
                      init_sha256=hashlib.sha256(entries['init'][1]).hexdigest(),
                      busybox_sha256=hashlib.sha256(entries['usr/bin/busybox'][1]).hexdigest(),
                      initramfs_entries=len(entries), applets=sorted(applet_names(entries['usr/bin/busybox'][1])))
        if args.quiet_boot:
            validate_quiet_boot(entries, args.initramfs)
            report['quiet_boot'] = 'PASS'
        if args.qemu:
            report['runtime'] = runtime(entries, args.qemu)
    if args.boot_script:
        validate_script(args.boot_script.read_bytes())
        report['boot_script'] = 'PASS'
    output = json.dumps(report, indent=2) + '\n'
    if args.report:
        args.report.write_text(output)
    print(output, end='')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, struct.error, subprocess.SubprocessError) as exc:
        raise SystemExit('GaoOS boot artifact validation failed: ' + str(exc))
