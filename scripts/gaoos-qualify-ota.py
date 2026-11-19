#!/usr/bin/env python3
"""Derive two test-only RGB30 OTA candidates from one validated factory image."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

spec = importlib.util.spec_from_file_location('gaoos_validate_image', Path(__file__).with_name('gaoos-validate-image.py'))
image_validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_validation)

SAFE_INTEGER = 2**53 - 1
QUALIFICATION_STATUS = 'QUALIFICATION_ARTIFACT_VALIDATED'
DATA_MARKER = b'# START OF DATA - DO NOT MODIFY\n'
VERSION = re.compile(rb'[a-zA-Z0-9][a-zA-Z0-9._+-]{0,95}\n')
REPLACED = (b'etc/gaoos-version', b'etc/gaoos-ota-sequence', b'etc/os-release')
MARKER = b'etc/gaoos-ota-qualification'


def run(*argv):
    result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return result.stdout


def validate_baseline(image, report, work):
    info = report.get('ota')
    image_validation.require(report.get('status') == 'PASS' and isinstance(info, dict),
                             'A complete factory OTA validation report is required')
    image_validation.require((info.get('board'), info.get('arch'), info.get('boot_layout')) ==
                             ('powkiddy,rgb30', 'aarch64', 'gaoos-rk3566-ab-v1'), 'Unexpected factory target')
    image_validation.require(report.get('boot', {}).get('expected_rgb30_argument') ==
                             'gaoos.fdt=device_trees/rk3566-powkiddy-rgb30.dtb',
                             'Factory validation does not establish the new RGB30 boot script')
    image_digest = image_validation.sha256(image)
    image_validation.require(image_digest == report['inputs']['image']['sha256'],
                             'Factory image SHA256 differs from validation')
    disk = work / 'factory.img'
    image_validation.expand_image(image, disk)
    partitions = image_validation.inspect_gpt(disk)
    boot, slot_a = partitions[:2]

    fat = work / 'boot.fat'
    image_validation.region(disk, boot['offset'], boot['bytes'], fat)
    for component, filename in [('kernel', 'KERNEL'), ('dtb', 'device_trees/rk3566-powkiddy-rgb30.dtb')]:
        path = work / component
        run('mcopy', '-i', fat, f'::/{filename}', path)
        image_validation.require(image_validation.sha256(path) == info[f'{component}_sha256'],
                                 f'Factory {component} differs from validation')

    size = info['size_bytes']
    image_validation.require(type(size) is int and 96 <= size <= slot_a['bytes'], 'Factory SquashFS exceeds slot A')
    system = work / 'system.squashfs'
    digest = image_validation.region(disk, slot_a['offset'], size, system)
    image_validation.require(digest == info['sha256'] == report['inputs']['system']['sha256'],
                             'Factory slot A differs from the validated SquashFS')
    image_validation.require(run('unsquashfs', '-cat', system, 'etc/gaoos-version') == info['version'].encode() + b'\n',
                             'Factory version differs from validation')
    image_validation.require(run('unsquashfs', '-cat', system, 'etc/gaoos-ota-sequence') ==
                             f'{info["sequence"]}\n'.encode(), 'Factory sequence differs from validation')

    source = {'image_sha256': image_digest, 'system_sha256': digest, 'git_sha': info['git_sha']}
    return system, info, source, slot_a['bytes']


def squashfs_settings(system):
    stats = run('unsquashfs', '-s', system)
    compressor = re.search(rb'^Compression (gzip|lzo|lz4|xz|zstd|lzma)$', stats, re.MULTILINE)
    block_size = re.search(rb'^Block size ([0-9]+)$', stats, re.MULTILINE)
    image_validation.require(compressor and block_size, 'Unsupported factory SquashFS compression')
    return compressor[1].decode(), block_size[1].decode()


def write_pseudo(source, target, replacements):
    # The generated metadata uses offsets relative to the start of its data
    # section. Keep all original data at the same offsets and append new bytes.
    edits = set()
    offset = source.stat().st_size
    with source.open('rb') as original, target.open('wb') as output:
        header = []
        while line := original.readline():
            header.append(line)
            if line == DATA_MARKER:
                final_marker = original.readline()
                image_validation.require(final_marker == b'#\n', 'Malformed pseudo data marker')
                header.append(final_marker)
                offset -= original.tell()
                break
        else:
            raise ValueError('Missing SquashFS pseudo data block')

        modified = []
        additions = bytearray()
        for line in header[:-3]:
            path, _, detail = line.partition(b' ')
            if path == MARKER:
                raise ValueError('Factory image already contains a qualification marker')
            if path in REPLACED and detail.startswith(b'R '):
                definition = detail.split()
                image_validation.require(len(definition) == 8 and path not in edits,
                                         'Unexpected version or sequence pseudo definition')
                data = replacements[path]
                modified.append(b' '.join([path, *definition[:5], str(len(data)).encode(),
                                           str(offset + len(additions)).encode(), b'0']) + b'\n')
                additions.extend(data)
                edits.add(path)
            else:
                modified.append(line)

        image_validation.require(edits == set(REPLACED), 'Missing regular factory identity file')
        marker = replacements[MARKER]
        modified.append(b' '.join([MARKER, b'R 0 644 0 0', str(len(marker)).encode(),
                                   str(offset + len(additions)).encode(), b'0']) + b'\n')
        output.writelines([*modified, *header[-3:]])
        shutil.copyfileobj(original, output, length=image_validation.CHUNK)
        output.write(additions + marker)


def qualification_candidate(system, pseudo, destination, info, source, capacity, sequence, number):
    suffix = f'.qa{number}'
    version = info['version'][:96 - len(suffix)] + suffix
    version_data = version.encode('ascii') + b'\n'
    image_validation.require(VERSION.fullmatch(version_data), 'Invalid qualification version')
    seq_data = f'{sequence}\n'.encode()
    release = run('unsquashfs', '-cat', system, 'etc/os-release')
    release, count = re.subn(rb'^OS_VERSION="[^"\n]*"$',
                             b'OS_VERSION="' + version_data.rstrip() + b'"', release, flags=re.MULTILINE)
    image_validation.require(count == 1, 'Factory os-release version must be set exactly once')
    marker = json.dumps({'status': 'TEST_ONLY', 'sequence': sequence, 'source': source},
                        sort_keys=True, separators=(',', ':')).encode() + b'\n'
    replacements = dict(zip(REPLACED, (version_data, seq_data, release)))
    replacements[MARKER] = marker

    compressor, block = squashfs_settings(system)
    artifact = destination / f'{version}-{sequence}.squashfs'
    with tempfile.TemporaryDirectory(prefix='gaoos-candidate-', dir=pseudo.parent) as directory:
        edited = Path(directory) / 'candidate.pseudo'
        write_pseudo(pseudo, edited, replacements)
        # Keep the codec and block geometry. Qualification builds prioritize
        # repeatable local turnaround and verify the exact physical capacity.
        compression_level = {'gzip': '6', 'zstd': '9'}.get(compressor)
        options = ['-Xcompression-level', compression_level] if compression_level else []
        run('mksquashfs', '-', artifact, '-pf', edited, '-noappend', '-no-progress', '-quiet',
            '-processors', str(min(os.cpu_count() or 2, 8)), '-comp', compressor, '-b', block, *options)
    size = artifact.stat().st_size
    image_validation.require(96 <= size <= capacity, 'Qualification SquashFS exceeds physical slot capacity')
    for path, value in replacements.items():
        image_validation.require(run('unsquashfs', '-cat', artifact, path.decode()) == value,
                                 f'Qualification contents differ at {path.decode()}')

    qualified = dict(info, version=version, sequence=sequence, size_bytes=size, sha256=image_validation.sha256(artifact))
    report = {
        'status': QUALIFICATION_STATUS,
        'classification': 'TEST_ONLY',
        'hardware': 'HARDWARE_BOOT_UNVERIFIED',
        'source': source,
        'artifact': artifact.name,
        'ota': qualified,
    }
    artifact.with_suffix('.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, required=True, help='Validated factory .img.gz')
    parser.add_argument('--report', type=Path, required=True, help='Validation with --ota-commit and --ota-sequence')
    parser.add_argument('--output', type=Path, required=True, help='New directory for test-only artifacts')
    parser.add_argument('--first-sequence', type=int, help='First trial sequence, defaults to factory sequence plus one')
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        image_validation.require(not output.exists() and output.parent.is_dir(), 'Choose a new output directory')
        report = json.loads(args.report.read_text())
        with tempfile.TemporaryDirectory(prefix='gaoos-qualification-', dir=output.parent) as directory:
            work = Path(directory)
            system, info, source, capacity = validate_baseline(args.image, report, work)
            initial = info['sequence']
            first = args.first_sequence if args.first_sequence is not None else initial + 1
            image_validation.require(0 <= initial < first < SAFE_INTEGER, 'Trial sequences must follow the factory sequence')
            pseudo = work / 'factory.pseudo'
            run('unsquashfs', '-no-progress', '-processors', '2', '-pf', pseudo, system)
            bundle = work / 'bundle'
            bundle.mkdir()
            for number, sequence in enumerate((first, first + 1), start=1):
                qualification_candidate(system, pseudo, bundle, info, source, capacity, sequence, number)
            bundle.rename(output)
    except (OSError, ValueError, KeyError, TypeError, EOFError, subprocess.SubprocessError) as exc:
        detail = exc.stderr.decode(errors='replace').strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        print(f'OTA qualification failed: {detail}', file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
