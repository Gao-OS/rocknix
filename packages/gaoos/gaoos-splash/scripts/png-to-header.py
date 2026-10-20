#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0
"""Embed the non-interlaced RGB GaoOS PNG without a runtime image library."""
from pathlib import Path
import struct
import sys
import zlib


def read_png(path):
    data = Path(path).read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('expected PNG input')
    offset, compressed = 8, bytearray()
    while offset < len(data):
        length = struct.unpack_from('>I', data, offset)[0]
        kind = data[offset+4:offset+8]
        chunk = data[offset+8:offset+8+length]
        if zlib.crc32(kind + chunk) != struct.unpack_from('>I', data, offset+8+length)[0]:
            raise ValueError('PNG checksum mismatch')
        if kind == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            if depth not in (8, 16) or color != 2 or compression or filtering or interlace:
                raise ValueError('expected non-interlaced 8/16-bit RGB PNG')
        elif kind == b'IDAT':
            compressed.extend(chunk)
        elif kind == b'IEND':
            break
        offset += length + 12
    stride = 3 * depth // 8
    row_size = width * stride
    decoded = zlib.decompress(compressed)
    if len(decoded) != height * (row_size + 1):
        raise ValueError('unexpected PNG data size')
    previous, pixels = bytearray(row_size), bytearray()
    for y in range(height):
        start = y * (row_size + 1)
        kind, row = decoded[start], bytearray(decoded[start+1:start+1+row_size])
        if kind > 4:
            raise ValueError('unsupported PNG row filter')
        for i in range(row_size):
            left = row[i-stride] if i >= stride else 0
            up = previous[i]
            upper_left = previous[i-stride] if i >= stride else 0
            estimate = left + up - upper_left
            distances = [abs(estimate-left), abs(estimate-up), abs(estimate-upper_left)]
            paeth = [left, up, upper_left][distances.index(min(distances))]
            predictor = [0, left, up, (left+up)//2, paeth][kind]
            row[i] = (row[i] + predictor) & 255
        if depth == 8:
            pixels.extend(row)
        else:
            pixels.extend((sample[0] + 128) // 257 for sample in struct.iter_unpack('>H', row))
        previous = row
    return width, height, bytes(pixels)


if __name__ == '__main__':
    width, height, pixels = read_png(sys.argv[1])
    with open(sys.argv[2], 'w') as header:
        header.write('/* Generated from the GaoOS logo PNG. */\n')
        header.write(f'enum {{ logo_width = {width}, logo_height = {height} }};\n')
        header.write('static const unsigned char logo_rgb[] = {\n')
        for offset in range(0, len(pixels), 24):
            header.write('    ' + ','.join(str(x) for x in pixels[offset:offset+24]) + ',\n')
        header.write('};\n')
