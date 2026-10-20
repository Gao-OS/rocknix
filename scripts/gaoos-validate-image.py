#!/usr/bin/env python3
"""Inspect a factory GaoOS RK3566 Generic A/B image without mounts or loop devices.

All inputs are read-only. Filesystem checkers operate on temporary copies. A PASS
establishes structural/artifact consistency, never RGB30 hardware boot acceptance.
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib

SECTOR, MIB, CHUNK = 512, 1024 * 1024, 4 * 1024 * 1024
LAYOUT = [('boot', 256), ('slot_a', 1536), ('slot_b', 1536), ('storage', 32)]
BOOT_START = 32768
REPO = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for data in iter(lambda: source.read(CHUNK), b''):
            digest.update(data)
    return digest.hexdigest()


def read_at(path, offset, size):
    with path.open('rb') as source:
        source.seek(offset)
        data = source.read(size)
    require(len(data) == size, f'Truncated image at byte {offset}')
    return data


def region(path, offset, size, destination=None, zero=False):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        source.seek(offset)
        output = destination.open('wb') if destination else None
        try:
            remaining = size
            while remaining:
                data = source.read(min(CHUNK, remaining))
                require(data, f'Truncated region at byte {offset}')
                require(not zero or not data.strip(b'\0'), f'Expected zero-filled region at byte {offset}')
                digest.update(data)
                if output:
                    output.write(data)
                remaining -= len(data)
        finally:
            if output:
                output.close()
    return digest.hexdigest()


def gpt_header(path, lba, sectors):
    data = read_at(path, lba * SECTOR, SECTOR)
    (magic, revision, size, checksum, reserved, current, backup, first, last,
     guid, entries_lba, count, entry_size, entries_crc) = struct.unpack_from('<8sIIIIQQQQ16sQIII', data)
    require(magic == b'EFI PART' and revision == 0x10000, f'Invalid GPT header at LBA {lba}')
    require(92 <= size <= SECTOR and reserved == 0, 'Invalid GPT header dimensions')
    header = bytearray(data[:size])
    header[16:20] = bytes(4)
    require(zlib.crc32(header) == checksum, f'GPT header CRC mismatch at LBA {lba}')
    require(current == lba and 0 <= backup < sectors and 1 < first <= last < sectors - 1,
            'Invalid GPT usable/header LBA bounds')
    require(0 < count <= 4096 and 128 <= entry_size <= 4096 and entry_size % 8 == 0,
            'Invalid GPT entry dimensions')
    require(entries_lba * SECTOR + count * entry_size <= sectors * SECTOR, 'GPT entries exceed image')
    entries = read_at(path, entries_lba * SECTOR, count * entry_size)
    require(zlib.crc32(entries) == entries_crc, f'GPT entry-array CRC mismatch at LBA {lba}')
    return dict(current=current, backup=backup, first=first, last=last, guid=guid,
                entries_lba=entries_lba, count=count, entry_size=entry_size, entries=entries)


def inspect_gpt(path):
    size = path.stat().st_size
    require(size == (BOOT_START // 2048 + sum(size for _, size in LAYOUT) + 1) * MIB,
            'Image size differs from RK3566 A/B factory layout')
    sectors = size // SECTOR
    mbr = read_at(path, 0, SECTOR)
    require(mbr[510:512] == b'\x55\xaa' and
            any(mbr[446 + i * 16 + 4] == 0xee for i in range(4)), 'Missing protective GPT MBR')
    primary, backup = gpt_header(path, 1, sectors), gpt_header(path, sectors - 1, sectors)
    require(primary['backup'] == sectors - 1 and backup['backup'] == 1, 'Invalid GPT backup linkage')
    for field in ('first', 'last', 'guid', 'count', 'entry_size', 'entries'):
        require(primary[field] == backup[field], f'GPT primary/backup mismatch: {field}')
    require(2 <= primary['entries_lba'] and
            primary['entries_lba'] * SECTOR + len(primary['entries']) <= primary['first'] * SECTOR,
            'Primary GPT entries overlap usable space')
    require(backup['last'] < backup['entries_lba'] and
            backup['entries_lba'] * SECTOR + len(backup['entries']) <= (sectors - 1) * SECTOR,
            'Backup GPT entries overlap usable space/header')
    partitions, identities, expected_start = [], set(), BOOT_START
    for index in range(primary['count']):
        entry = primary['entries'][index * primary['entry_size']:(index + 1) * primary['entry_size']]
        if entry[:16] == bytes(16):
            continue
        require(len(partitions) < 4 and index == len(partitions), 'Expected GPT partitions in entries 1-4 only')
        name, mib = LAYOUT[index]
        first, last = struct.unpack_from('<QQ', entry, 32)
        label = entry[56:128].decode('utf-16le').split('\0', 1)[0]
        require(label == name and first == expected_start and last == first + mib * MIB // SECTOR - 1,
                f'Invalid GPT extent/label for partition {index + 1}: {label}')
        require(primary['first'] <= first <= last <= primary['last'], 'Partition outside usable GPT space')
        identity = uuid.UUID(bytes_le=entry[16:32])
        require(identity.int != 0 and identity not in identities, 'Invalid/duplicate partition GUID')
        identities.add(identity)
        partitions.append(dict(index=index + 1, name=name, first=first, last=last,
                               offset=first * SECTOR, bytes=mib * MIB, guid=str(identity)))
        expected_start = last + 1
    require(len(partitions) == 4, 'Expected exactly four GPT partitions')
    return partitions


def assignments(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        require(separator and key and key not in result and key.replace('_', '').isalnum(),
                f'Invalid/duplicate assignment in {path.name}: {line}')
        result[key] = ' '.join(shlex.split(value, comments=True))
    return result


def inspect(args, report, work):
    logs = args.report.parent / (args.report.stem + '-logs')
    logs.mkdir(parents=True, exist_ok=True)

    def command(argv, name, timeout=600):
        argv = list(map(str, argv))
        require(shutil.which(argv[0]), f'Required validation tool unavailable: {argv[0]}')
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        log = logs / (name + '.log')
        log.write_bytes(result.stdout)
        report.setdefault('commands', []).append(dict(command=argv, exit=result.returncode, log=str(log)))
        require(result.returncode == 0, f'{name} failed (exit {result.returncode}); see {log}')
        return result.stdout.decode(errors='replace').strip()

    for name in ('image', 'kernel', 'uboot', 'system'):
        path = getattr(args, name)
        require(path.is_file() and path.stat().st_size > 0, f'Missing/empty input: {path}')
    report['inputs'] = {key: dict(path=str(getattr(args, key)), sha256=sha256(getattr(args, key)))
                        for key in ('image', 'kernel', 'uboot', 'system')}
    disk, total = work / 'disk.img', 0
    with gzip.open(args.image, 'rb') as source, disk.open('wb') as output:
        for data in iter(lambda: source.read(CHUNK), b''):
            # Sparse output bounds temporary disk use without skipping gzip CRC.
            if data.strip(b'\0'):
                output.write(data)
            else:
                output.seek(len(data), os.SEEK_CUR)
            total += len(data)
        output.truncate(total)
    report['gzip'] = dict(status='PASS', bytes=total)
    partitions = inspect_gpt(disk)
    report['gpt'] = partitions
    uboot_size = args.uboot.stat().st_size
    require(64 * SECTOR + uboot_size <= partitions[0]['offset'], 'U-Boot overlaps the boot partition')
    require(region(disk, 64 * SECTOR, uboot_size) == report['inputs']['uboot']['sha256'],
            'Embedded U-Boot at LBA 64 differs from intended Generic bootloader')
    report['uboot'] = 'PASS'

    fat, boot = work / 'boot.fat', work / 'boot'
    region(disk, partitions[0]['offset'], partitions[0]['bytes'], fat)
    header = read_at(fat, 0, SECTOR)
    require(header[510:512] == b'\x55\xaa' and struct.unpack_from('<H', header, 11)[0] == SECTOR,
            'Invalid FAT boot sector')
    require((struct.unpack_from('<H', header, 19)[0] or struct.unpack_from('<I', header, 32)[0]) * SECTOR == partitions[0]['bytes'],
            'FAT size differs from boot partition')
    require(header[71:82].strip() == b'GAOOS', 'Incorrect FAT32 volume label')
    command(['fsck.fat', '-n', fat], 'fat-fsck')
    listing = command(['mdir', '-i', fat, '-/', '-b', '::'], 'fat-list').lower()
    require(not any(item.rstrip('/') in ('::/system', '::/system.md5', '::/extlinux') or
                    item.startswith('::/extlinux/') for item in listing.splitlines()),
            'Legacy SYSTEM/extlinux boot fallback exists in factory A/B image')
    boot.mkdir()
    for name in ('KERNEL', 'boot.scr', 'ab_state.env', 'gaoos_boot.env'):
        command(['mcopy', '-i', fat, '::/' + name, boot / name], 'extract-' + name)
    command(['mcopy', '-i', fat, '-s', '::/device_trees', boot], 'extract-dtbs')
    require(sha256(boot / 'KERNEL') == report['inputs']['kernel']['sha256'], 'FAT KERNEL differs from rebuilt Image')
    metadata = assignments(boot / 'gaoos_boot.env')
    require(set(metadata) == {'extra_cmdline', 'gaoos_fdt', 'gaoos_fdtdir', 'gaoos_overlays'} and
            metadata['gaoos_fdt'] == '' and metadata['gaoos_fdtdir'] == 'device_trees' and
            metadata['gaoos_overlays'] == '', 'Invalid Generic device-tree metadata')
    unsafe = ('debugging', 'recovery', 'break=', 'systemd.debug_shell', 'gaoos.slot=', 'gaoos.dev=', 'boot=', 'disk=')
    require(not any(token == value or token.startswith(value) for token in metadata['extra_cmdline'].split() for value in unsafe),
            'Production Generic metadata exposes debugging or overrides A/B boot arguments')
    defaults = REPO / 'packages/gaoos/gaoos-ab-boot/boot/ab_state.env.default'
    require(assignments(boot / 'ab_state.env') == assignments(defaults), 'Factory A/B state differs from approved defaults')
    script = (boot / 'boot.scr').read_bytes()
    require(len(script) >= 72, 'Truncated boot.scr')
    size, terminator = struct.unpack_from('>II', script, 64)
    require(terminator == 0 and script[72:72 + size] == (defaults.parent / 'boot.cmd').read_bytes() and
            not script[72 + size:].strip(b'\0'), 'boot.scr differs from current boot.cmd')
    compiled_init = (REPO / 'projects/ROCKNIX/packages/sysutils/busybox/scripts/init').read_bytes()
    for token, value in ((b'DISTRONAME', b'GaoOS'), (b'DEVICENAME', b'RK3566'),
                         (b'KERNEL_NAME', b'KERNEL'), (b'GAOOS_AB_BOOT', b'yes')):
        compiled_init = compiled_init.replace(b'@' + token + b'@', value)
    require(b'GAOOS_AB_MODE="yes"' in compiled_init, 'Current source lacks compiled GaoOS A/B boot mode')
    if args.init:
        require(args.init.read_bytes() == compiled_init, 'Staged init differs from current compiled source')
    current_init = work / 'current-init'
    current_init.write_bytes(compiled_init)
    command([sys.executable, REPO / 'scripts/gaoos-validate-boot.py', '--kernel', boot / 'KERNEL',
             '--boot-script', boot / 'boot.scr', '--init', current_init] +
            (['--busybox', args.busybox] if args.busybox else []), 'embedded-boot-artifacts')
    rgb30 = boot / 'device_trees/rk3566-powkiddy-rgb30.dtb'
    require(rgb30.is_file(), 'RGB30 DTB missing from Generic image')
    model = command(['fdtget', '-t', 's', rgb30, '/', 'model'], 'rgb30-model')
    compatible = command(['fdtget', '-t', 's', rgb30, '/', 'compatible'], 'rgb30-compatible')
    require('rgb30' in model.lower() and 'powkiddy,rgb30' in compatible and 'rockchip,rk3566' in compatible,
            'Generic RGB30 DTB has wrong model/compatible')
    report['boot'] = dict(status='PASS', model=model, compatible=compatible, metadata=metadata)

    size = args.system.stat().st_size
    require(96 <= size <= partitions[1]['bytes'], 'SquashFS does not fit A/B slot')
    require(region(disk, partitions[1]['offset'], size) == report['inputs']['system']['sha256'],
            'Slot A differs from intended SquashFS payload')
    region(disk, partitions[1]['offset'] + size, partitions[1]['bytes'] - size, zero=True)
    region(disk, partitions[2]['offset'], partitions[2]['bytes'], zero=True)
    rootfs = work / 'rootfs'
    command(['unsquashfs', '-no-progress', '-processors', '2', '-d', rootfs, args.system],
            'squashfs-full-extraction', timeout=1800)
    release = assignments(rootfs / 'etc/os-release')
    require(release.get('OS_NAME') == 'GaoOS' and release.get('HW_DEVICE') == 'RK3566' and
            release.get('HW_ARCH') == 'aarch64', 'SquashFS distribution/device/architecture mismatch')
    require((rootfs / 'usr/share/bootloader/boot.scr').read_bytes() == script, 'FAT/rootfs boot.scr mismatch')
    require(sha256(rootfs / 'usr/share/bootloader/Generic_uboot.bin') == report['inputs']['uboot']['sha256'],
            'Rootfs Generic bootloader differs from image bootloader')
    require((rootfs / 'usr/lib/systemd/systemd').is_file(), 'Rootfs systemd missing')
    package = REPO / 'packages/gaoos/gaoos-ab-boot'
    for name in ('gaoos-update', 'gaoos-boot-confirm'):
        installed = rootfs / 'usr/bin' / name
        require(installed.is_file() and not installed.is_symlink() and
                installed.read_bytes() == (package / 'src' / name).read_bytes(),
                f'Rootfs {name} differs from current package source')
        require(installed.stat().st_mode & 0o111, f'Rootfs {name} is not executable')
    unit = 'gaoos-boot-confirm.service'
    require((rootfs / 'usr/lib/systemd/system' / unit).read_bytes() ==
            (package / 'system.d' / unit).read_bytes(), 'Rootfs confirmation unit differs from current source')
    enabled = rootfs / 'usr/lib/systemd/system/multi-user.target.wants' / unit
    require(enabled.is_symlink() and os.readlink(enabled) == '../' + unit,
            'Boot confirmation service is not enabled')
    report['slots'] = dict(status='PASS', slot_a_sha256=report['inputs']['system']['sha256'],
                           slot_a_padding='ZERO', slot_b='EMPTY_ZERO', full_extraction='PASS')

    storage = work / 'storage.ext4'
    region(disk, partitions[3]['offset'], partitions[3]['bytes'], storage)
    sb = read_at(storage, 1024, 1024)
    require(struct.unpack_from('<H', sb, 56)[0] == 0xef53 and sb[120:136].rstrip(b'\0') == b'STORAGE',
            'Invalid ext4 storage magic/label')
    command(['e2fsck', '-f', '-n', storage], 'storage-e2fsck')
    report['storage'] = 'PASS'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('image', 'kernel', 'uboot', 'system', 'report'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--init', type=Path, help='Expected staged init for embedded byte comparison')
    parser.add_argument('--busybox', type=Path, help='Expected staged BusyBox for embedded byte comparison')
    args = parser.parse_args()
    args.report = args.report.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = dict(status='FAIL', hardware='HARDWARE_BOOT_UNVERIFIED')
    try:
        with tempfile.TemporaryDirectory(prefix='gaoos-image-', dir=args.report.parent) as td:
            inspect(args, report, Path(td))
        report['status'] = 'PASS'
    except (ValueError, OSError, EOFError, UnicodeError, struct.error, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in ('status', 'hardware', 'error') if key in report}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
