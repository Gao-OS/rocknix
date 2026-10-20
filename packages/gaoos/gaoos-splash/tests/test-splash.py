#!/usr/bin/env python3
"""Host checks for GaoOS initramfs selection, PNG generation and framebuffer output."""
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zlib
import sys
sys.dont_write_bytecode = True

PACKAGE = Path(__file__).resolve().parents[1]
ROOT = PACKAGE.parents[2]
UPSTREAM = ROOT / 'build.GaoOS-RK3566.aarch64/build/rocknix-splash-9d295bcb74be2282e32c6b614efbea6036974ba8'


class SplashTests(unittest.TestCase):
    def test_distro_selection(self):
        script = ROOT / 'projects/ROCKNIX/packages/virtual/initramfs/package.mk'
        for distro, expected, excluded in [('GaoOS', 'gaoos-splash:init', 'rocknix-splash:init'),
                                           ('ROCKNIX', 'rocknix-splash:init', 'gaoos-splash:init')]:
            result = subprocess.check_output(['bash', '-c',
                'get_pkg_directory() { :; }; source "$1"; echo "$PKG_DEPENDS_INIT"',
                'bash', str(script)], env={**os.environ, 'DISTRO': distro}, text=True)
            self.assertIn(expected, result.split())
            self.assertNotIn(excluded, result.split())

    def test_png_generation(self):
        generator = PACKAGE / 'scripts/png-to-header.py'
        self.assertTrue(generator.is_file(), 'build-time GaoOS PNG generator is missing')
        spec = importlib.util.spec_from_file_location('png_to_header', generator)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        width, height, pixels = module.read_png(ROOT / 'distributions/GaoOS/logos/gaoos-logo.png')
        self.assertEqual((width, height), (400, 120))
        # Independent ImageMagick 8-bit RGB conversion of the source asset.
        self.assertEqual(hashlib.sha256(pixels).hexdigest(),
                         '2bb206f1765dc975b85156ade13038006917ae7b9a0d4a151b3f6979e85e80ae')
        # Exercise all PNG row filters, including 16-bit RGB sample stride.
        for depth in (8, 16):
            stride = 3 * depth // 8
            row = bytes([12, 24, 36, 48, 60, 72]) if depth == 8 else bytes([12, 12, 24, 24, 36, 36, 48, 48, 60, 60, 72, 72])
            for filter_type in range(5):
                encoded = bytearray()
                for i, value in enumerate(row):
                    left = row[i-stride] if i >= stride else 0
                    # Identical second row gives up=value, upper-left=left.
                    predictor = [0, left, value, (left + value)//2, value][filter_type]
                    encoded.append((value-predictor) & 255)
                def chunk(kind, data):
                    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
                png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, depth, 2, 0, 0, 0))
                       + chunk(b'IDAT', zlib.compress(b'\0' + row + bytes([filter_type]) + encoded)) + chunk(b'IEND', b''))
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / 'fixture.png'
                    path.write_bytes(png)
                    self.assertEqual(module.read_png(path), (2, 2, bytes([12,24,36,48,60,72])*2))

    def test_real_framebuffer_render(self):
        renderer = PACKAGE / 'src/render.c'
        self.assertTrue(renderer.is_file(), 'GaoOS framebuffer renderer is missing')
        # Upstream files are fetched by the normal package build. Accept a supplied
        # extracted source directory so this test also runs in a clean checkout.
        upstream = Path(os.environ.get('ROCKNIX_SPLASH_SOURCE', UPSTREAM))
        self.assertTrue((upstream / 'fbsplash.c').is_file(),
                        'set ROCKNIX_SPLASH_SOURCE to the pinned, extracted splash archive')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            # An asymmetric image exposes incorrect orientation and aspect ratio.
            (directory / 'logo.h').write_text('enum { logo_width=4, logo_height=2 };\n'
                'static const unsigned char logo_rgb[]={16,32,48,255,0,0,255,255,0,255,0,255,0,255,0,0,0,255,0,255,255,255,255,255};\n')
            harness = directory / 'test.c'
            harness.write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fbsplash.h"
#include "render.h"
int main(int argc, char **argv) {
    (void)argc;
    Framebuffer fb = {0};
    fb.vinfo.xres=atoi(argv[1]); fb.vinfo.yres=atoi(argv[2]);
    fb.vinfo.bits_per_pixel=atoi(argv[4]); fb.vinfo.xoffset=2; fb.vinfo.yoffset=1;
    fb.finfo.line_length=(fb.vinfo.xres+4)*(fb.vinfo.bits_per_pixel/8);
    fb.screensize=(fb.vinfo.yres+2)*fb.finfo.line_length;
    fb.buffer=malloc(fb.screensize+16); memset(fb.buffer,0xa5,fb.screensize+16);
    render_logo(&fb,atoi(argv[3]));
    fwrite(fb.buffer,1,fb.screensize+16,stdout); free(fb.buffer); return 0;
}
''')
            executable = directory / 'render-test'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                '-I'+str(upstream), '-I'+str(PACKAGE / 'src'), '-I'+str(directory), str(harness),
                str(renderer), str(upstream / 'fbsplash.c'), '-o', str(executable)], check=True)
            colors = [0x102030, 0xff0000, 0xffff00, 0xff00ff, 0x00ff00, 0x0000ff, 0x00ffff, 0xffffff]
            for width, height in [(720,720), (640,480), (480,640)]:
                for rotation in (0,90,180,270):
                    for bits in (16,32):
                        with self.subTest(width=width, height=height, rotation=rotation, bits=bits):
                            raw = subprocess.check_output([str(executable), str(width), str(height), str(rotation), str(bits)])
                            logical_width, logical_height = (height,width) if rotation in (90,270) else (width,height)
                            draw_width = logical_width*4//5
                            draw_height = draw_width//2
                            if draw_height > logical_height*4//5:
                                draw_height = logical_height*4//5
                                draw_width = draw_height*2
                            left, top = (logical_width-draw_width)//2, (logical_height-draw_height)//2
                            bpp, pitch = bits//8, (width+4)*(bits//8)
                            expected = bytearray(b'\xa5'*len(raw))
                            for y in range(height):
                                for x in range(width):
                                    color=colors[0]
                                    lx,ly=x,y
                                    if rotation == 90:
                                        lx,ly=y,width-1-x
                                    elif rotation == 180:
                                        lx,ly=width-1-x,height-1-y
                                    elif rotation == 270:
                                        lx,ly=height-1-y,x
                                    if left <= lx < left+draw_width and top <= ly < top+draw_height:
                                        sx,sy=(lx-left)*4//draw_width,(ly-top)*2//draw_height
                                        color=colors[sy*4+sx]
                                    if bits == 16:
                                        color=((color>>19)<<11)|(((color>>10)&63)<<5)|((color>>3)&31)
                                    offset=(y+1)*pitch+(x+2)*bpp
                                    expected[offset:offset+bpp]=color.to_bytes(bpp,'little')
                            self.assertEqual(raw, expected)


if __name__ == '__main__':
    unittest.main()
