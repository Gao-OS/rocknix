# CLAUDE.md — GaoOS RockNix Fork

## What is this?

GaoOS RockNix is a fork of [ROCKNIX/distribution](https://github.com/ROCKNIX/distribution),
an immutable Linux distribution for handheld gaming devices. GaoOS adds an A/B boot system
for atomic, rollback-safe OTA updates.

**Upstream**: ROCKNIX (fork of JELOS, fork of CoreELEC/LibreELEC)
**Fork**: Gao-OS/rocknix
**Organization**: github.com/Gao-OS

## Key Architectural Difference: A/B Boot

Upstream ROCKNIX uses a single system partition overwritten in-place during updates.
GaoOS uses a dual-slot A/B partition layout:

```
p1: boot (FAT32)    — kernel, dtb, boot.scr, ab_state.env
p2: slot_a (SquashFS) — system image A (read-only)
p3: slot_b (SquashFS) — system image B (read-only)
p4: storage (EXT4)   — configs, ROMs, saves, overlay (read-write)
```

Updates write to the inactive slot. U-Boot reads ab_state.env to select
the active slot. Auto-rollback after 3 failed boots.

## Build System

The build system is inherited from CoreELEC/LibreELEC. Key concepts:

- **Packages**: Each software component has a `package.mk` in `packages/`
- **Projects**: Hardware-specific config (bootloader, kernel, dtb) in `projects/`
- **Distributions**: Distro identity (name, branding, package list) in `distributions/`
- **Build command**: `PROJECT=Rockchip DEVICE=RK3566 ARCH=aarch64 ./scripts/build_distro`
- **Docker**: `make docker-RK3566` builds inside a container
- **First build**: ~10 hours. Subsequent: minutes (cached).

## GaoOS-Specific Files

Changes from upstream are isolated to minimize merge conflicts:

| Path | Purpose | Conflict risk |
|------|---------|---------------|
| `distributions/GaoOS/` | Distro config, branding, package list | None (additive) |
| `packages/gaoos/gaoos-ab-boot/` | A/B boot package | None (additive) |
| `scripts/image` | Modified for 4-partition layout | Medium |
| `Makefile` | GaoOS options path | Low |
| `FORK_WORKFLOW.md` | Fork maintenance guide | None |

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
  # Install files into ${INSTALL} prefix
  mkdir -p ${INSTALL}/usr/bin
  cp ${PKG_DIR}/src/gaoos-update ${INSTALL}/usr/bin/
}
```

## A/B Boot Components

- `boot.cmd` → compiled to `boot.scr` via mkimage
- `ab_state.env` — slot state file (active_slot, status, retry counters)
- `gaoos-update` — CLI for status/apply/confirm/rollback/canary
- `gaoos-boot-confirm.service` — systemd oneshot, confirms healthy boot after 30s

## Supported Devices (inherited from ROCKNIX)

Build targets in Makefile: RK3326, RK3566, RK3588, RK3399, S922X, SM8250, SM8550, H700, AMD64

GaoOS initial target: **RK3566** (Anbernic RG353 series)

