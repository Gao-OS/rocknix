# AGENTS.md

Guidance for coding agents working in this repository.

## Global Preferences

### Git Worktrees

When creating git worktrees, always place them in the current project's `.trees`
directory, for example `<project-root>/.trees/<branch-name>`.

### Git Commits
### Git Commits

- Do not add agent-specific trailers (e.g. `Co-Authored-By: <agent>`, `Generated
  with <agent>`) to commit messages unless the user explicitly asks.

### Parallelism

- Default to parallel subagent execution for decomposable work.
- Only serialize tasks when they have explicit dependencies.
- Batch file reads, writes, and tool calls whenever the API allows.

### PRD Scope Rules

- When working from a PRD, only modify files within the specified scope.
- Run only scoped tests, for example `mix test test/<module>/`, unless told
  otherwise.
- If tests outside scope fail, list them and stop; do not attempt to fix them.
- When all in-scope tests pass and the PRD checklist is complete, stop.

### Spec Kit

This repository does not use Spec Kit `.specify/` artifacts. Do not create or
update `.specify/` unless the user explicitly asks for Spec Kit setup.

## Upstream Dependency Issue Routing

When working in this repo, if you encounter a bug or missing feature in a
dependency hosted under any of these GitHub organizations:

- `gsmlgorg`
- `gsmlg-dev`
- `duskmoon-dev`
- `gsmlg-app`
- `Gao-OS`
- `gsmlg-opt`
- `gsmlg-ci`
- `gsmlg-games`
- `gsmlg-com`

Do not work around it silently. Instead:

1. Identify the upstream repo from the package source. Check `mix.exs`,
   `package.json`, `Cargo.toml`, `go.mod`, `pubspec.yaml`, `flake.nix`, or
   `flake.lock` as applicable.
2. Create an issue in that upstream repo with `gh issue create`.
3. Set the issue type with `--type "Bug"` or `--type "Feature"`.
4. Apply the label `internal request`, creating it if needed.
5. Use title format `[internal] <concise description>`.
6. Include the requesting repo and branch, need, reproduction or expected
   behavior, and severity: `blocker`, `needed`, or `nice-to-have`.
7. In the current repo, add a TODO or workaround comment at the callsite:
   `# TODO(upstream): org/repo#<issue_number>` or
   `# WORKAROUND(upstream): org/repo#<issue_number>`.

If the upstream issue is a blocker, stop work on the blocked task and report
which upstream issue it depends on. For `needed` and `nice-to-have`, implement
a temporary local workaround marked with the upstream issue.

## Project Overview

GaoOS RockNix is a fork of
[ROCKNIX/distribution](https://github.com/ROCKNIX/distribution), an immutable
Linux distribution for handheld gaming devices. GaoOS adds an A/B boot system
for atomic, rollback-safe OTA updates.

- Upstream: ROCKNIX, forked from JELOS, forked from CoreELEC/LibreELEC.
- Fork: `Gao-OS/rocknix`.
- Organization: `github.com/Gao-OS`.

## Development Environment

```bash
devenv shell
direnv allow
```

Use `devenv shell` for the full build dependency environment. `direnv allow`
can activate it automatically when configured.

## Build Commands

Full RK3566 image build, primary GaoOS target:

```bash
DEVICE_ROOT=RK3566 DISTRO=GaoOS PROJECT=ROCKNIX DEVICE=RK3566 ARCH=aarch64 ./scripts/build_distro
```

Makefile build for RK3566, which builds both `arm` and `aarch64`:

```bash
make RK3566
```

Docker build:

```bash
make docker-RK3566
```

Docker shell:

```bash
make docker-shell
```

Build a single package:

```bash
PACKAGE=gaoos-ab-boot make package
./scripts/build gaoos-ab-boot
```

Clean a single package:

```bash
PACKAGE=gaoos-ab-boot make package-clean
./scripts/clean gaoos-ab-boot
```

Generate the disk image only after packages are built:

```bash
make image
```

Clean build artifacts:

```bash
make clean
make distclean
```

## Testing

A/B boot integration tests can run from the repo root without a device:

```bash
bash packages/gaoos/gaoos-ab-boot/tests/test-fr9-integration.sh
```

There is no project-wide test suite or linter. Tests are per-package shell
scripts.

## A/B Boot Architecture

Upstream ROCKNIX uses a single system partition overwritten in place during
updates. GaoOS uses a dual-slot A/B partition layout:

```text
p1: boot    FAT32     kernel, dtb, boot.scr, ab_state.env
p2: slot_a  SquashFS  system image A, read-only
p3: slot_b  SquashFS  system image B, read-only
p4: storage EXT4      configs, ROMs, saves, overlay, read-write
```

Updates write to the inactive slot. U-Boot reads `ab_state.env` to select the
active slot. Auto-rollback occurs after three failed boots.

## Build System Architecture

The build system is inherited from CoreELEC/LibreELEC:

- Packages: component directories with `package.mk` under `packages/`.
- Projects: hardware-specific config in `projects/`.
- Distributions: distro identity, branding, and package lists in
  `distributions/`.
- `config/options`: sourced by scripts to load project, device, and distro
  settings.
- `scripts/build`: builds a single package with dependency tracking and stamp
  caching.
- `scripts/image` and `scripts/mkimage`: assemble the final disk image.
- Build artifacts land in `build.*` directories.

Environment variables control the build: `DISTRO`, `PROJECT`, `DEVICE`, and
`ARCH`. `PROJECT=ROCKNIX` is correct for GaoOS builds; it selects hardware
project configs under `projects/`, not the distribution.

## GaoOS-Specific Files

Keep GaoOS changes isolated to minimize upstream merge conflicts.

| Path | Purpose | Conflict risk |
| --- | --- | --- |
| `distributions/GaoOS/` | Distro config, branding, package list, A/B boot vars | None, additive |
| `packages/gaoos/gaoos-ab-boot/` | A/B boot package, update CLI, boot script, partitioner, ES wrappers | None, additive |
| `scripts/image` | Passes A/B vars, writes device identity files | Low |
| `scripts/mkimage` | Four-partition layout behind `GAOOS_AB_BOOT` | Medium |
| `projects/ROCKNIX/.../busybox/scripts/init` | `gaoos.slot=` cmdline parsing and A/B mount | Medium |
| `projects/ROCKNIX/devices/RK3566/bootloader/update.sh` | OTA `boot.scr` update | Low |
| `.github/workflows/build-nightly.yml` | GaoOS release and manifest generation | Medium |
| `Makefile` | GaoOS Docker image and options path | Low |

## Branch Strategy

- `gaoos`: release branch, always buildable.
- `gaoos-dev`: development work.
- `upstream-sync`: temporary branch for merging upstream ROCKNIX changes.

## Critical Rules

1. Never blind find-replace `ROCKNIX`. It appears as branding, build paths, and
   attribution.
2. Keep A/B changes behind the `GAOOS_AB_BOOT` flag so upstream image script
   changes merge cleanly.
3. Put GaoOS packages in `packages/gaoos/`, not mixed into upstream package
   directories.
4. Keep distribution config in `distributions/GaoOS/`, separate from
   `distributions/ROCKNIX/`.
5. Test on RK3566 first; it is the initial and best-supported GaoOS target.
6. Use `PROJECT=ROCKNIX` for GaoOS builds.

## Package Format

Every package is a directory with `package.mk`.

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

- `boot.cmd` compiles to `boot.scr` for U-Boot slot selection.
- `ab_state.env` stores `active_slot`, slot status, and retry counters.
- `gaoos-update` handles status, apply, confirm, rollback, and canary flows.
- `gaoos-boot-confirm.service` confirms a healthy boot after delay.
- EmulationStation wrapper commands redirect ROCKNIX update flows to
  `gaoos-update`.

## Supported Devices

Build targets: RK3326, RK3566, RK3588, RK3399, S922X, SM8250, SM8550, H700,
and SM8650.

GaoOS initial target: RK3566, including the Anbernic RG353 series.
