#!/usr/bin/env python3
"""Compile the patched U-Boot FIT matcher without a cross compiler or build tree."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
PATCH = ROOT / 'projects/ROCKNIX/devices/RK3566/packages/u-boot-Generic/patches/003-fix-dtb-and-vs.patch'
BOARD = Path('board/anbernic/rgxx3_rk3566/rgxx3-rk3566.c')

# Minimal verbatim excerpts from u-boot v2026.01's board source. Apply the
# distribution patch first, then compile its actual matcher with board-ID stubs.
# Keep the first excerpt at its upstream line number for patch's offset search.
UPSTREAM_HEAD = '\n' * 35 + '''#define GPIO_WRITEMASK(bits)\t((bits) << 16)

#define DTB_DIR\t\t\t"rockchip/"

struct rg3xx_model {
\tconst u16 adc_value;
};
'''

UPSTREAM_MATCHER = '''int board_fit_config_name_match(const char *name)
{
\tint ret;

\tif (gd->board_type == 0) {
\t\tret = rgxx3_detect_device();
\t\tif (ret < 0)
\t\t\treturn ret;
\t\tgd->board_type = ret;
\t}

\tif (strcmp(name, rg3xx_model_details[gd->board_type].fdtfile) == 0)
\t\treturn 0;

\treturn -ENXIO;
}
'''
UPSTREAM = (UPSTREAM_HEAD + '\n' * (714 - UPSTREAM_HEAD.count('\n'))
            + UPSTREAM_MATCHER)


class RGB30Boot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        directory = Path(cls.temp.name)
        source = directory / BOARD
        source.parent.mkdir(parents=True)
        source.write_text(UPSTREAM)
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(PATCH)],
                       cwd=directory, check=True, capture_output=True, text=True)
        patched = source.read_text()
        dtb_dir = re.search(r'^#define DTB_DIR.*$', patched, re.M).group(0)
        matcher = patched[patched.index('int board_fit_config_name_match('):]
        harness = directory / 'matcher.c'
        harness.write_text('''#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
''' + dtb_dir + '''
struct global_data { int board_type; } state, *gd = &state;
struct { const char *fdtfile; } rg3xx_model_details[] = {
    { NULL },
    { DTB_DIR "rk3566-powkiddy-rgb30.dtb" },
    { DTB_DIR "rk3566-anbernic-rg353p.dtb" },
};
int rgxx3_detect_device(void) { return 1; }
''' + matcher + '''
int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    gd->board_type = atoi(argv[1]);
    printf("%d\\n%s\\n", board_fit_config_name_match(argv[2]),
           rg3xx_model_details[1].fdtfile);
    return 0;
}
''')
        cls.matcher = directory / 'matcher'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', str(harness),
                        '-o', str(cls.matcher)], check=True, capture_output=True,
                       text=True)

    def check_match(self, board, name, matches):
        result = subprocess.run([str(self.matcher), str(board), name],
                                check=True, capture_output=True, text=True)
        code, linux_fdt = result.stdout.splitlines()
        self.assertEqual(code == '0', matches, result.stdout)
        self.assertEqual(linux_fdt, 'rk3566-powkiddy-rgb30.dtb')

    def test_rgb30_matches_prefixed_fit_after_board_detection(self):
        self.check_match(0, 'rockchip/rk3566-powkiddy-rgb30.dtb', True)

    def test_rgb30_matches_bare_fit(self):
        self.check_match(1, 'rk3566-powkiddy-rgb30.dtb', True)

    def test_another_board_matches_prefixed_fit(self):
        self.check_match(2, 'rockchip/rk3566-anbernic-rg353p.dtb', True)

    def test_rgb30_rejects_other_board(self):
        self.check_match(1, 'rockchip/rk3566-anbernic-rg353p.dtb', False)


if __name__ == '__main__':
    unittest.main()
