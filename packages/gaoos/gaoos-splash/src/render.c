/* SPDX-License-Identifier: GPL-2.0 */
#include "render.h"
#include "logo.h"

void render_logo(Framebuffer *fb, int rotation) {
    /* Fit in the unrotated display, then rotate clockwise like ROCKNIX. */
    uint32_t width = fb->vinfo.xres, height = fb->vinfo.yres;
    uint32_t logical_width = rotation == 90 || rotation == 270 ? height : width;
    uint32_t logical_height = rotation == 90 || rotation == 270 ? width : height;
    uint32_t draw_width = logical_width * 4 / 5;
    uint32_t draw_height = (uint64_t)draw_width * logo_height / logo_width;
    if (draw_height > logical_height * 4 / 5) {
        draw_height = logical_height * 4 / 5;
        draw_width = (uint64_t)draw_height * logo_width / logo_height;
    }
    uint32_t left = (logical_width - draw_width) / 2;
    uint32_t top = (logical_height - draw_height) / 2;
    uint32_t background = (logo_rgb[0] << 16) | (logo_rgb[1] << 8) | logo_rgb[2];
    for (uint32_t y = 0; y < height; y++) {
        for (uint32_t x = 0; x < width; x++) {
            uint32_t lx = x, ly = y;
            switch (rotation) {
                case 90: lx = y; ly = width - 1 - x; break;
                case 180: lx = width - 1 - x; ly = height - 1 - y; break;
                case 270: lx = height - 1 - y; ly = x; break;
            }
            uint32_t color = background;
            if (lx >= left && lx - left < draw_width &&
                ly >= top && ly - top < draw_height) {
                uint32_t sx = (uint64_t)(lx - left) * logo_width / draw_width;
                uint32_t sy = (uint64_t)(ly - top) * logo_height / draw_height;
                const unsigned char *pixel = &logo_rgb[(sy * logo_width + sx) * 3];
                color = (pixel[0] << 16) | (pixel[1] << 8) | pixel[2];
            }
            set_pixel(fb, x, y, color);
        }
    }
}
