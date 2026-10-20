# GaoOS early splash

`/usr/bin/gaoos-splash` draws once on `/dev/fb0`, uses the panel rotation from
`/proc/device-tree`, and exits. The initramfs caller waits for a framebuffer and
chooses whether to show a splash. A missing framebuffer or unsupported pixel
depth returns a nonzero status.

The package pins ROCKNIX/rocknix-splash at
`9d295bcb74be2282e32c6b614efbea6036974ba8`, with the same GPL license and SHA256
as the upstream distribution package. Only its framebuffer and device-tree
rotation sources are compiled. GaoOS supplies its own entry point and renderer;
the upstream compiled SVG branding and branding integrity code are omitted.
`PKG_SOURCE_NAME` retains the upstream archive name for the distribution source
mirror when the original GitHub archive is unavailable.

The build converts `distributions/GaoOS/logos/gaoos-logo.png` to an embedded RGB
array with Python's standard library. There is no runtime PNG, SVG, DRM, SDL, or
font dependency. The source asset is an opaque non-interlaced 16-bit RGB PNG;
the converter supports 8-bit and 16-bit RGB PNGs and all five PNG row filters.

The renderer fills the display with the asset's background color and fits the
logo, preserving its 400:120 aspect ratio, within 80% of the logical display.
It rotates the completed layout clockwise by 0, 90, 180, or 270 degrees to avoid
clipping on portrait panels. Framebuffer formats retain upstream support:
32-bit packed RGB and 16-bit RGB565, with framebuffer pitch and offsets.
Other packed channel layouts are not supported by the upstream pixel writer.

Run the host regressions from the repository root:

```sh
python3 packages/gaoos/gaoos-splash/tests/test-splash.py
```

The render test compiles the actual upstream framebuffer source. It defaults to
the existing RK3566 build source directory. For a clean checkout, extract the
pinned archive and set `ROCKNIX_SPLASH_SOURCE` to that extracted directory.
Synthetic render checks qualify pixels and layout; physical panel appearance
still requires an RGB30 boot test.
