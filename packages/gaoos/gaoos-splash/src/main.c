/* SPDX-License-Identifier: GPL-2.0 */
#include <stdio.h>
#include "render.h"
#include "dt_rotation.h"

int main(void) {
    Framebuffer *fb = fb_init("/dev/fb0");
    if (!fb)
        return 1;
    if (fb->vinfo.bits_per_pixel != 16 && fb->vinfo.bits_per_pixel != 32) {
        fprintf(stderr, "Unsupported framebuffer depth: %u\n", fb->vinfo.bits_per_pixel);
        fb_cleanup(fb);
        return 1;
    }
    render_logo(fb, get_display_rotation());
    fb_flush(fb);
    fb_cleanup(fb);
    return 0;
}
