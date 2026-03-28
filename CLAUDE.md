# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is this?

GaoOS RockNix is a fork of [ROCKNIX/distribution](https://github.com/ROCKNIX/distribution),
an immutable Linux distribution for handheld gaming devices. GaoOS adds an A/B boot system
for atomic, rollback-safe OTA updates.

**Upstream**: ROCKNIX (fork of JELOS, fork of CoreELEC/LibreELEC)
**Fork**: Gao-OS/rocknix
**Organization**: github.com/Gao-OS

## Development Environment

```bash
devenv shell    # enter dev shell with all build deps (requires devenv + nix)
direnv allow    # or use direnv for automatic activation
```

## Build Commands

Full distro build for a device (first build ~10 hours, subsequent minutes via cache):
```bash
# Direct build (primary target)
DEVICE_ROOT=RK3566 DISTRO=GaoOS PROJECT=ROCKNIX DEVICE=RK3566 ARCH=aarch64 ./scripts/build_distro

# Via Makefile (builds both arm + aarch64)
make RK3566

# Docker build
make docker-RK3566

# Docker shell (interactive)
make docker-shell
```

Build a single package:
```bash
PACKAGE=gaoos-ab-boot make package

# Or directly:
./scripts/build gaoos-ab-boot
```

Clean a single package (forces rebuild):
```bash
PACKAGE=gaoos-ab-boot make package-clean
# Or: ./scripts/clean gaoos-ab-boot
```

Generate the disk image only (after packages are built):
```bash
make image
```

Full clean / distclean:
```bash
make clean       # remove build artifacts
make distclean   # remove everything including downloads
```

## Testing

A/B boot integration tests (run from repo root, no device needed):
```bash
bash packages/gaoos/gaoos-ab-boot/tests/test-fr9-integration.sh
```

There is no project-wide test suite or linter. Tests are per-package shell scripts.

## Key Architectural Difference: A/B Boot

Upstream ROCKNIX uses a single system partition overwritten in-place during updates.
GaoOS uses a dual-slot A/B partition layout:

```
p1: boot (FAT32)    — kernel, dtb, boot.scr, ab_state.env
p2: slot_a (SquashFS) — system image A (read-only)
p3: slot_b (SquashFS) — system image B (read-only)
p4: storage (EXT4)   — configs, ROMs, saves, overlay (read-write)
```

Updates write to the inactive slot. U-Boot reads `ab_state.env` to select
the active slot. Auto-rollback after 3 failed boots.

## Build System Architecture

The build system is inherited from CoreELEC/LibreELEC:

- **Packages**: Each component is a dir with `package.mk` under `packages/`
- **Projects**: Hardware-specific config (bootloader, kernel, dtb) in `projects/`
- **Distributions**: Distro identity, branding, package list in `distributions/`
- `config/options` — sourced by every script; loads project/device/distro settings
- `scripts/build` — builds a single package (with dependency tracking + stamp caching)
- `scripts/image` → `scripts/mkimage` — assembles the final disk image
- Build artifacts land in `build.*` dirs; stamps prevent re-building unchanged packages

Environment variables control everything: `DISTRO`, `PROJECT`, `DEVICE`, `ARCH`.
Note: PROJECT is always `ROCKNIX` even for GaoOS builds (it selects the hardware project dir).

## GaoOS-Specific Files

Changes from upstream are isolated to minimize merge conflicts:

| Path | Purpose | Conflict risk |
|------|---------|---------------|
| `distributions/GaoOS/` | Distro config, branding, package list, A/B boot vars | None (additive) |
| `packages/gaoos/gaoos-ab-boot/` | A/B boot package (update CLI, boot.scr, partitioner, ES wrappers) | None (additive) |
| `scripts/image` | Passes A/B vars, writes device identity files | Low |
| `scripts/mkimage` | 4-partition layout behind `GAOOS_AB_BOOT` flag | Medium |
| `projects/ROCKNIX/.../busybox/scripts/init` | `gaoos.slot=` cmdline parsing + A/B mount | Medium |
| `projects/ROCKNIX/devices/RK3566/bootloader/update.sh` | OTA boot.scr update | Low |
| `.github/workflows/build-nightly.yml` | GaoOS release + manifest generation | Medium |
| `Makefile` | GaoOS docker image + options path | Low |

## Branch Strategy

- `gaoos` — release branch, always buildable
- `gaoos-dev` — development work
- `upstream-sync` — temporary branch for merging upstream ROCKNIX changes

## Critical Rules

1. **Never blind find-replace ROCKNIX** — it appears as branding (replace),
   build paths (replace carefully), and attribution (preserve).
2. **Keep A/B changes behind GAOOS_AB_BOOT flag** — so upstream image script
   changes merge cleanly.
3. **GaoOS packages go in `packages/gaoos/`** — NOT mixed into upstream package dirs.
4. **Distribution config is `distributions/GaoOS/`** — separate from upstream's
   `distributions/ROCKNIX/`.
5. **Test on RK3566 first** — it's the most common and best-supported target.
6. **PROJECT=ROCKNIX is correct** — even for GaoOS builds. PROJECT selects hardware
   configs under `projects/`, not the distribution.

## Package Format

Every package is a directory with `package.mk`. Key variables:

```bash
PKG_NAME="gaoos-ab-boot"
PKG_VERSION="1.0.0"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/Gao-OS/rocknix"
PKG_DEPENDS_TARGET="toolchain"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
  mkdir -p ${INSTALL}/usr/bin
  cp ${PKG_DIR}/src/gaoos-update ${INSTALL}/usr/bin/
}
```

## A/B Boot Components

- `boot.cmd` → compiled to `boot.scr` via mkimage
- `ab_state.env` — slot state file (active_slot, status, retry counters)
- `gaoos-update` — CLI for status/apply/confirm/rollback/canary
- `gaoos-boot-confirm.service` — systemd oneshot, confirms healthy boot after 30s
- ES integration wrappers (`rocknix-update`, `updatecheck`, `system-upgrade`) — redirect
  EmulationStation's update flow to `gaoos-update`

## Supported Devices

Build targets: RK3326, RK3566, RK3588, RK3399, S922X, SM8250, SM8550, H700, SM8650

GaoOS initial target: **RK3566** (Anbernic RG353 series)
