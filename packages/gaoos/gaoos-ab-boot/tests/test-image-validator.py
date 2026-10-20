#!/usr/bin/env python3
"""Exercise real GPT parsing with sparse factory images and corrupt metadata."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location('gaoos_image_validator', ROOT / 'scripts/gaoos-validate-image.py')
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)
SECTOR = VALIDATOR.SECTOR


class ImageGPT(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gaoos-gpt-regression-')
        self.addCleanup(self.temp.cleanup)
        self.disk = Path(self.temp.name) / 'image.img'
        self.sectors = (VALIDATOR.BOOT_START // 2048 + sum(size for _, size in VALIDATOR.LAYOUT) + 1) * 2048
        entries = bytearray(128 * 128)
        first = VALIDATOR.BOOT_START
        for i, (name, mib) in enumerate(VALIDATOR.LAYOUT):
            last = first + mib * 2048 - 1
            entry = (uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le +
                     uuid.UUID(int=i + 1).bytes_le + struct.pack('<QQQ', first, last, 0) +
                     name.encode('utf-16le').ljust(72, b'\0'))
            entries[i * 128:(i + 1) * 128] = entry
            first = last + 1
        self.entries = bytes(entries)
        with self.disk.open('wb') as stream:
            stream.truncate(self.sectors * SECTOR)
            mbr = bytearray(SECTOR)
            mbr[450] = 0xee
            mbr[510:512] = b'\x55\xaa'
            stream.write(mbr)
        self.write_table(1, self.entries)
        self.write_table(self.sectors - 1, self.entries)

    def write_table(self, lba, entries):
        backup = self.sectors - 1 if lba == 1 else 1
        entries_lba = 2 if lba == 1 else self.sectors - 33
        header = bytearray(struct.pack('<8sIIIIQQQQ16sQIII', b'EFI PART', 0x10000, 92, 0, 0,
                                       lba, backup, 34, self.sectors - 34,
                                       uuid.UUID(int=123).bytes_le, entries_lba, 128, 128,
                                       zlib.crc32(entries)))
        struct.pack_into('<I', header, 16, zlib.crc32(header))
        with self.disk.open('r+b') as stream:
            stream.seek(lba * SECTOR)
            stream.write(header)
            stream.seek(entries_lba * SECTOR)
            stream.write(entries)

    def test_complete_factory_gpt(self):
        table = VALIDATOR.inspect_gpt(self.disk)
        self.assertEqual([entry['name'] for entry in table], ['boot', 'slot_a', 'slot_b', 'storage'])
        self.assertEqual(table[2]['bytes'], 1536 * VALIDATOR.MIB)

    def test_corrupted_header_crc(self):
        with self.disk.open('r+b') as stream:
            stream.seek(SECTOR + 56)
            stream.write(b'\xff')
        with self.assertRaisesRegex(ValueError, 'header CRC'):
            VALIDATOR.inspect_gpt(self.disk)

    def test_corrupted_entry_array_crc(self):
        with self.disk.open('r+b') as stream:
            stream.seek(2 * SECTOR + 56)
            stream.write(b'\xff')
        with self.assertRaisesRegex(ValueError, 'entry-array CRC'):
            VALIDATOR.inspect_gpt(self.disk)

    def test_individually_valid_but_divergent_backup(self):
        entries = bytearray(self.entries)
        entries[56:58] = b'X\0'
        self.write_table(self.sectors - 1, entries)
        with self.assertRaisesRegex(ValueError, 'primary/backup mismatch'):
            VALIDATOR.inspect_gpt(self.disk)

    def test_overlapping_slot_extent_with_valid_crcs(self):
        entries = bytearray(self.entries)
        struct.pack_into('<Q', entries, 128 + 32, VALIDATOR.BOOT_START)
        self.write_table(1, entries)
        self.write_table(self.sectors - 1, entries)
        with self.assertRaisesRegex(ValueError, 'extent/label'):
            VALIDATOR.inspect_gpt(self.disk)

    def test_wrong_gpt_partition_count_with_valid_crcs(self):
        entries = bytearray(self.entries)
        entries[3 * 128:4 * 128] = bytes(128)
        self.write_table(1, entries)
        self.write_table(self.sectors - 1, entries)
        with self.assertRaisesRegex(ValueError, 'exactly four'):
            VALIDATOR.inspect_gpt(self.disk)

    def test_truncated_image(self):
        with self.disk.open('r+b') as stream:
            stream.truncate(self.sectors * SECTOR - SECTOR)
        with self.assertRaisesRegex(ValueError, 'Image size'):
            VALIDATOR.inspect_gpt(self.disk)


if __name__ == '__main__':
    unittest.main(verbosity=2)
