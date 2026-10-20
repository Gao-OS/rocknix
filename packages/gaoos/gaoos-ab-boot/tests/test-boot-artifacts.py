#!/usr/bin/env python3
"""Reject stale or broken embedded boot artifacts before image publication."""
import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[4]


def cpio(files):
    result = bytearray()
    for name, mode, content in [*files, ('TRAILER!!!', 0, b'')]:
        name = name.encode() + b'\0'
        fields = [0, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name), 0]
        result.extend(b'070701' + ''.join('%08x' % x for x in fields).encode() + name)
        result.extend(b'\0' * (-len(result) % 4))
        result.extend(content)
        result.extend(b'\0' * (-len(result) % 4))
    return bytes(result)


class BootArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('artifacts', ROOT / 'scripts/gaoos-validate-boot.py')
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def image(self, init=b'#!/bin/sh\n', applets=None, fsck_target=None):
        applets = applets or self.module.REQUIRED_APPLETS
        binary = bytearray(64)
        binary[:5] = b'\x7fELF\x02'
        struct.pack_into('<H', binary, 18, 183)
        binary = bytes(binary) + b'ash\0awk\0' + b'\0'.join(
            a.encode() for a in sorted(set(applets) - {'ash', 'awk'})) + b'\0\0'
        files = [('init', 0o100755, init), ('usr/bin/busybox', 0o100755, binary),
                 ('usr/bin/sh', 0o120777, b'busybox')]
        files += [(name, 0o120777, fsck_target) if name == 'usr/sbin/fsck.ext4' and fsck_target is not None
                  else (name, 0o100755, b'executable') for name in self.module.REQUIRED_EXTERNALS]
        if fsck_target is not None:
            files.append(('usr/sbin/e2fsck', 0o100755, b'executable'))
        archive = cpio(files)
        header = bytearray(64)
        header[56:60] = b'ARM\x64'
        image = header + archive
        struct.pack_into('<Q', image, 16, len(image))
        return bytes(image)

    def test_valid_kernel_and_embedded_initramfs(self):
        self.module.validate_kernel(self.image(), expected_init=b'#!/bin/sh\n')

    def test_linux_gen_init_cpio_null_terminated_symlink(self):
        entries = self.module.validate_kernel(self.image(fsck_target=b'e2fsck\0'))
        self.assertEqual(entries['usr/sbin/fsck.ext4'][1], b'e2fsck')

    def test_interior_null_symlink_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.validate_kernel(self.image(fsck_target=b'e2\0fsck'))

    def test_missing_readlink_even_when_elf_imports_it(self):
        image = bytearray(self.image(applets=self.module.REQUIRED_APPLETS - {'readlink'}) + b'\0readlink\0')
        struct.pack_into('<Q', image, 16, len(image))
        with self.assertRaisesRegex(ValueError, 'readlink'):
            self.module.validate_kernel(image)

    def test_stale_init_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'init.*differs'):
            self.module.validate_kernel(self.image(), expected_init=b'new init')

    def test_kernel_without_initramfs_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'initramfs'):
            self.module.validate_kernel(self.image()[:64])

    def test_invalid_image_header_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'AArch64'):
            self.module.validate_kernel(b'corrupt kernel')

    def test_truncated_archive_is_rejected(self):
        with self.assertRaises(ValueError):
            self.module.validate_kernel(self.image()[:-20])

    def test_script_crcs_are_checked(self):
        content = struct.pack('>II', 10, 0) + b'echo boot\n'
        header = bytearray(64)
        struct.pack_into('>7I', header, 0, 0x27051956, 0, 0, len(content), 0, 0,
                         zlib.crc32(content))
        header[28:32] = bytes([5, 22, 6, 0])
        struct.pack_into('>I', header, 4, zlib.crc32(header))
        self.module.validate_script(bytes(header) + content)
        with self.assertRaisesRegex(ValueError, 'CRC'):
            self.module.validate_script(bytes(header) + content[:-1] + b'x')


if __name__ == '__main__':
    unittest.main(verbosity=2)
