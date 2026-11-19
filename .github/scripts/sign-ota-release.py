#!/usr/bin/env python3
"""Sign the exact OTA v1 manifest derived from a validated RGB30 factory build."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

SAFE_INTEGER = 2**53 - 1
MAX_SIZE = 2**32
ED25519_SPKI_PREFIX = bytes.fromhex('302a300506032b6570032100')
PLATFORM = {'board': 'powkiddy,rgb30', 'arch': 'aarch64', 'boot_layout': 'gaoos-rk3566-ab-v1'}
OTA_FIELDS = {*PLATFORM, 'sequence', 'version', 'git_sha', 'kernel_sha256', 'dtb_sha256', 'sha256', 'size_bytes'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def url_base64(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def build_manifest(args):
    report = json.loads(args.report.read_bytes())
    if args.allow_qualification:
        require(report.get('status') == 'QUALIFICATION_ARTIFACT_VALIDATED' and
                report.get('classification') == 'TEST_ONLY' and args.channel == 'nightly',
                'Qualification signing requires a test-only report and the isolated nightly channel')
    else:
        require(report.get('status') == 'PASS', 'Factory image validation did not pass')
    ota = report.get('ota')
    require(isinstance(ota, dict) and set(ota) == OTA_FIELDS, 'Missing validated OTA identity')
    require(all(ota[key] == value for key, value in PLATFORM.items()), 'Unexpected OTA platform')
    require(ota['git_sha'] == args.commit and re.fullmatch(r'[a-f0-9]{40}', args.commit),
            'OTA validation source commit mismatch')
    require(type(ota['sequence']) is int and ota['sequence'] == args.sequence and
            0 <= args.minimum_sequence < args.sequence <= SAFE_INTEGER, 'Invalid OTA sequence policy')
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,95}', ota['version']), 'Invalid version')
    rollback_from = [int(value) for value in args.rollback_from.split(',')] if args.rollback_from else []
    require(len(rollback_from) <= 32 and len(set(rollback_from)) == len(rollback_from) and
            all(args.sequence < value <= SAFE_INTEGER for value in rollback_from),
            'Invalid rollback source sequences')
    require(0 < args.issued_at < args.expires_at <= SAFE_INTEGER and
            args.expires_at - args.issued_at <= 90 * 86400, 'Invalid manifest validity')
    require(all(re.fullmatch(r'[a-f0-9]{64}', ota[key])
                for key in ('sha256', 'kernel_sha256', 'dtb_sha256')), 'Invalid validated digest')
    require(type(ota['size_bytes']) is int and 96 <= ota['size_bytes'] <= MAX_SIZE and
            args.artifact.stat().st_size == ota['size_bytes'], 'Artifact size does not match validation')
    with args.artifact.open('rb') as artifact:
        require(artifact.read(4) == b'hsqs', 'Artifact is not SquashFS')
        artifact.seek(0)
        digest = hashlib.sha256()
        while chunk := artifact.read(4 * 1024 * 1024):
            digest.update(chunk)
        require(digest.hexdigest() == ota['sha256'], 'Artifact SHA256 does not match validation')
    return {
        **ota, 'schema_version': 1, 'channel': args.channel, 'minimum_sequence': args.minimum_sequence,
        'rollback_from': sorted(rollback_from), 'issued_at': args.issued_at, 'expires_at': args.expires_at
    }


def openssl(argv, key):
    # The private PEM is available through the pipe only. Suppress diagnostics
    # from OpenSSL while reading key material.
    result = subprocess.run(['openssl', *argv], input=key, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=30)
    require(result.returncode == 0, 'OpenSSL refused the configured Ed25519 signing key')
    return result.stdout


def envelope(manifest, key):
    public = openssl(['pkey', '-in', '/dev/stdin', '-passin', 'pass:', '-pubout', '-outform', 'DER'], key)
    require(public.startswith(ED25519_SPKI_PREFIX) and
            len(public) == len(ED25519_SPKI_PREFIX) + 32, 'Signing key is not Ed25519')
    data = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    with tempfile.TemporaryDirectory(prefix='gaoos-ota-sign-') as directory:
        path = Path(directory) / 'manifest.json'
        path.write_bytes(data)
        signature = openssl(['pkeyutl', '-sign', '-rawin', '-in', str(path), '-inkey', '/dev/stdin',
                             '-passin', 'pass:'], key)
    require(len(signature) == 64, 'Invalid Ed25519 signature size')
    return dict(key_id=hashlib.sha256(public[-32:]).hexdigest(), manifest=url_base64(data),
                signature=url_base64(signature))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('report', 'artifact', 'output'):
        parser.add_argument('--' + field, type=Path, required=True)
    for field in ('sequence', 'issued-at', 'expires-at'):
        parser.add_argument('--' + field, type=int, required=True)
    parser.add_argument('--minimum-sequence', type=int, default=0)
    parser.add_argument('--rollback-from', default='')
    parser.add_argument('--allow-qualification', action='store_true',
                        help='Use only with a separate test trust set and OTA deployment')
    parser.add_argument('--commit', required=True)
    parser.add_argument('--channel', required=True, choices=('stable', 'beta', 'nightly'))
    args = parser.parse_args()
    try:
        manifest = build_manifest(args)
        key = os.environ.pop('GAOOS_OTA_SIGNING_KEY', '')
        require(bool(key), 'No OTA signing key is configured')
        signed = envelope(manifest, key.encode())
        args.output.write_text(json.dumps(signed, separators=(',', ':')) + '\n')
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        print(f'OTA signing failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
