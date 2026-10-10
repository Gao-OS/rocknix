# Product Requirements Document: GaoOS RockNix

**Project**: GaoOS — RockNix Branch
**Author**: Jonathan Gao
**Status**: Draft
**Created**: 2026-03-28
**Repository**: github.com/Gao-OS/rocknix
**Upstream**: github.com/ROCKNIX/distribution

---

## 1. Executive Summary

GaoOS RockNix is a fork of ROCKNIX, an immutable Linux distribution for handheld gaming devices. The fork introduces an A/B dual-slot boot system that eliminates the risk of bricking devices during over-the-air (OTA) updates. GaoOS RockNix is one of three branches in the GaoOS "Build Your Own OS" project, alongside LineageOS (Android) and NixOS branches.

The primary deliverable is a drop-in replacement for ROCKNIX that adds atomic, rollback-safe system updates while maintaining full compatibility with the upstream emulator ecosystem, device support matrix, and user workflow.

---

## 2. Problem Statement

ROCKNIX uses a single read-only SquashFS system partition that is overwritten in-place during updates. This design has three failure modes that result in an unbootable device:

1. **Power loss during write** — A partially written SquashFS image is unrecoverable. The device will not boot. The user must physically remove the SD card, reflash it from a computer, and re-add their games and configuration.

2. **Corrupt or wrong image** — ROCKNIX does not verify image checksums before applying, and users must manually select the correct image for their chipset (RK3326, RK3566, RK3588, etc.). Applying the wrong image bricks the device.

3. **Bad update with no rollback** — If a new system version has a bug that prevents boot (kernel panic, broken init, bad driver), there is no way to revert without reflashing. The user loses their current working system.

These failure modes are unacceptable for a consumer-facing device. Users of handheld gaming handhelds are often non-technical and may not have immediate access to a computer for reflashing.

---

## 3. Goals

### Primary Goals

- **G1**: Eliminate brick risk from OTA updates by ensuring the running system is never modified during an update.
- **G2**: Provide automatic rollback when a new system version fails to boot, with zero user intervention required.
- **G3**: Maintain full upstream ROCKNIX compatibility — all emulators, games, device support, and user workflows must work identically.
- **G4**: Establish a sustainable fork that can track upstream ROCKNIX releases with minimal merge conflict overhead.

### Secondary Goals

- **G5**: Provide a CLI-based update tool (`gaoos-update`) that can be integrated into EmulationStation's menu system.
- **G6**: Support checksum verification of update images before writing.
- **G7**: Enable "canary" deployments where an update is staged but the user controls when to reboot.
- **G8**: Serve as a reference implementation for A/B boot on embedded Linux gaming devices.

### Non-Goals

- Modifying the emulator stack or game compatibility
- Adding new device support beyond what upstream ROCKNIX provides
- Replacing the EmulationStation frontend
- Supporting A/B boot on Android-based handhelds (these already have Android's own A/B mechanism via ABL)

---

## 4. Target Users

### Primary: ROCKNIX Users

Handheld gaming enthusiasts running ROCKNIX on devices like Anbernic RG353, RG351, Gameforce Ace, ODROID Go Ultra, and similar. These users want hassle-free updates without risk. They may be non-technical and unfamiliar with SD card reflashing.

### Secondary: GaoOS Developers

Members of the Gao-OS GitHub organization who maintain and extend GaoOS across its three branches. Need a clean, well-documented fork that is easy to build, test, and release.

### Tertiary: Embedded Linux Community

Developers of similar gaming distributions (Batocera, KNULLI, muOS) who may adopt the A/B boot approach. The implementation should be well-documented and portable.

---

## 5. System Architecture

### 5.1 Partition Layout

Current ROCKNIX (3-partition):
```
[boot 64MB FAT32] [system ~600MB SquashFS RO] [storage EXT4 RW]
```

GaoOS A/B (4-partition):
```
[boot 64MB FAT32] [slot_a ~768MB SquashFS RO] [slot_b ~768MB SquashFS RO] [storage EXT4 RW]
```

Storage overhead: ~768MB additional for the second slot. On a 32GB SD card (minimum recommended), this represents ~2.4% of total capacity.

### 5.2 Boot Flow

```
Power On
  → U-Boot loads boot.scr from boot partition
  → boot.scr reads ab_state.env
  → Selects active slot (A or B)
  → If active slot status is "bad", falls back to other slot
  → If active slot status is "pending", increments retry counter
  → If retry counter >= 3, marks slot "bad" and falls back
  → Passes root=/dev/mmcblk0pN and gaoos.slot={a|b} to kernel
  → Kernel mounts SquashFS root
  → Systemd starts services
  → gaoos-boot-confirm.service runs after 30s
  → If health checks pass, marks slot "good" in ab_state.env
  → If health checks fail, slot stays "pending" for next boot
```

### 5.3 Slot State Machine

```
              apply update
  [empty] ─────────────────→ [pending]
                                 │
                    ┌────────────┼────────────┐
                    │ boot OK    │             │ 3 failures
                    ▼            │             ▼
                 [good]    retry + reboot    [bad]
                    │                         │
                    │      apply update       │
                    └────────────→ ←──────────┘
                              [pending]
```

States:
- **good** — Slot has been confirmed healthy. Safe to use as fallback.
- **pending** — Slot has been written but not yet confirmed. Will auto-rollback after `max_retries` failed boots.
- **bad** — Slot failed to boot. Will not be selected unless the other slot is also bad.

### 5.4 Component Map

| Component | Location | Language | Purpose |
|-----------|----------|----------|---------|
| boot.cmd / boot.scr | Boot partition | U-Boot script | Slot selection and retry logic |
| ab_state.env | Boot partition | Key-value text | Slot metadata (shared between U-Boot and userspace) |
| gaoos-update | /usr/bin/ | Bash | CLI for update management |
| gaoos-boot-confirm.service | systemd | Systemd unit | Auto-confirms healthy boots |
| gaoos-ab.conf | /storage/.config/ | Config file | Update server URL, preferences |
| scripts/image | Build system | Bash | Creates 4-partition SD card image |
| package.mk | Build system | Makefile/Bash | Integrates A/B components into build |

---

## 6. Functional Requirements

### FR1: Partition Management

- **FR1.1**: The image creation script MUST produce a 4-partition GPT layout: boot (FAT32, 64MB), slot_a (raw, 768MB), slot_b (raw, 768MB), storage (EXT4, remaining).
- **FR1.2**: The initial image MUST write the system SquashFS to slot_a only. Slot_b MUST be zeroed.
- **FR1.3**: The storage partition MUST be the last partition to consume remaining space.
- **FR1.4**: A partitioning script (`gaoos-partition.sh`) MUST be provided for creating the layout on bare SD cards.

### FR2: Boot Slot Selection

- **FR2.1**: U-Boot MUST read `ab_state.env` from the boot partition on every boot.
- **FR2.2**: U-Boot MUST select the slot indicated by `active_slot` if its status is "good" or "pending".
- **FR2.3**: If the active slot status is "bad", U-Boot MUST fall back to the other slot.
- **FR2.4**: If both slots are "bad", U-Boot MUST attempt slot A as a last resort.
- **FR2.5**: For "pending" slots, U-Boot MUST increment the retry counter and persist it to `ab_state.env` before booting.
- **FR2.6**: If a "pending" slot's retry counter reaches `max_retries` (default 3), U-Boot MUST mark it "bad" and fall back.
- **FR2.7**: U-Boot MUST pass `gaoos.slot={a|b}` on the kernel command line so userspace can identify the running slot.

### FR3: Update Application

- **FR3.1**: `gaoos-update apply <image>` MUST write the SquashFS image to the INACTIVE slot only. The active/running slot MUST NOT be modified.
- **FR3.2**: Before writing, `gaoos-update` MUST verify the image checksum if a `.sha256` file is present.
- **FR3.3**: Before writing, `gaoos-update` SHOULD validate the image is a valid SquashFS (via `unsquashfs -s`).
- **FR3.4**: After writing, `gaoos-update` MUST verify the written data by comparing the partition checksum against the source image.
- **FR3.5**: After successful write and verification, `gaoos-update` MUST set the inactive slot status to "pending", reset its retry counter to 0, and set `active_slot` to the newly written slot.
- **FR3.6**: `gaoos-update` MUST NOT automatically reboot. The user controls when to reboot.

### FR4: Boot Confirmation

- **FR4.1**: `gaoos-boot-confirm.service` MUST run as a systemd oneshot after multi-user.target.
- **FR4.2**: The service MUST wait at least 30 seconds after boot before running health checks, to allow services to stabilize.
- **FR4.3**: Health checks MUST verify: (a) the storage partition is mounted, (b) system uptime is at least 10 seconds.
- **FR4.4**: If all health checks pass, the service MUST mark the running slot as "good" and reset its retry counter.
- **FR4.5**: If any health check fails, the service MUST leave the slot as "pending" and log the failure reason.

### FR5: Manual Rollback

- **FR5.1**: `gaoos-update rollback` MUST switch `active_slot` to the other slot if that slot's status is not "bad".
- **FR5.2**: Rollback MUST NOT automatically reboot.
- **FR5.3**: If the target rollback slot is "bad", the command MUST fail with a clear error message.

### FR6: Status and Diagnostics

- **FR6.1**: `gaoos-update status` MUST display: running slot, active slot, both slots' status and retry counts, and any staged update.
- **FR6.2**: The running slot MUST be determined from `/proc/cmdline` (the `gaoos.slot=` parameter), not from `ab_state.env`.

### FR7: Update Discovery

- **FR7.1**: `gaoos-update check` MUST read the device identifier from `/etc/gaoos-device` and construct a per-device manifest URL.
- **FR7.2**: The manifest URL pattern MUST be configurable in `gaoos-ab.conf` with a `${DEVICE}` variable for substitution. Default: `https://github.com/Gao-OS/rocknix/releases/latest/download/manifest-${DEVICE}.json`.
- **FR7.3**: The manifest format MUST be JSON containing at minimum: `version`, `device`, `url`, `sha256`, `size_bytes`. Optional fields: `changelog_url`, `min_version`.
- **FR7.4**: `gaoos-update download` MUST download the image and its checksum to `/storage/.update/`.
- **FR7.5**: `gaoos-update canary` MUST execute the full download-verify-apply flow without rebooting.
- **FR7.6**: If `min_version` is present in the manifest and the running version is older than `min_version`, `gaoos-update` MUST warn the user that a fresh flash may be required and refuse to apply.

### FR8: Storage Partition Safety

- **FR8.1**: No update operation MUST ever write to the storage partition (p4), except for staging downloaded images in `/storage/.update/`.
- **FR8.2**: User data (ROMs, saves, configs, writable overlay) MUST survive all slot switches, updates, and rollbacks.

### FR9: EmulationStation Integration (M5)

- **FR9.1**: The existing ROCKNIX update flow in System Settings MUST be replaced, not augmented. Users MUST NOT see two competing update mechanisms.
- **FR9.2**: The System Settings → Software Update menu MUST expose: "Check for Updates", "Download & Apply", "Reboot to Update" (only after successful stage), "Rollback to Previous", and "System Status".
- **FR9.3**: The old `/storage/.update/*.tar` in-place overwrite detection in init scripts MUST be removed to prevent accidental bypass of A/B logic.
- **FR9.4**: Update progress (download percentage, write progress) SHOULD be displayed in EmulationStation when possible.

### FR10: Device Identity

- **FR10.1**: The build system MUST write the device identifier (e.g., `RK3566`) to `/etc/gaoos-device` at image creation time.
- **FR10.2**: The build system MUST write the GaoOS version (e.g., `20260401`) to `/etc/gaoos-version`.
- **FR10.3**: `gaoos-update` MUST use these files to construct manifest URLs and compare versions.

---

## 7. Non-Functional Requirements

### NFR1: Compatibility

- **NFR1.1**: GaoOS MUST build and boot on all devices supported by upstream ROCKNIX (RK3326, RK3566, RK3588, RK3399, S922X, SM8250, SM8550, H700).
- **NFR1.2**: All upstream ROCKNIX emulators and ports MUST function identically.
- **NFR1.3**: The EmulationStation frontend MUST work without modification.
- **NFR1.4**: Existing ROCKNIX user workflows (adding games via Samba/SCP, scraping, RetroAchievements) MUST work identically.

### NFR2: Performance

- **NFR2.1**: Boot time MUST NOT increase by more than 3 seconds compared to upstream ROCKNIX on the same device.
- **NFR2.2**: The gaoos-boot-confirm health check MUST complete within 5 seconds (excluding the 30s wait).
- **NFR2.3**: Update apply (write + verify) for a 500MB image MUST complete within 5 minutes on UHS-I SD cards.

### NFR3: Storage

- **NFR3.1**: The A/B layout MUST fit on a 16GB SD card with at least 12GB remaining for user storage.
- **NFR3.2**: Each slot MUST support system images up to 768MB.

### NFR4: Reliability

- **NFR4.1**: Power loss during `gaoos-update apply` MUST NOT affect the currently running slot or the storage partition. Only the target (inactive) slot may be left in an inconsistent state.
- **NFR4.2**: Power loss during boot (after U-Boot starts, before confirmation) MUST result in a retry on next power-on, eventually leading to auto-rollback if the slot is genuinely broken.
- **NFR4.3**: Corrupted `ab_state.env` MUST result in a safe fallback (U-Boot defaults to slot A with status "good").

### NFR5: Maintainability

- **NFR5.1**: GaoOS-specific changes MUST be isolated to `distributions/GaoOS/`, `packages/gaoos/`, and a conditional block in `scripts/image`.
- **NFR5.2**: Upstream ROCKNIX merges SHOULD produce conflicts in at most one file (`scripts/image`).
- **NFR5.3**: The fork MUST be buildable using upstream's Docker-based build system without additional host dependencies.

### NFR6: Licensing

- **NFR6.1**: All ROCKNIX branding (name, logos, splash screens) MUST be replaced with GaoOS equivalents. ROCKNIX branding is CC BY-NC-SA 4.0 and cannot be redistributed.
- **NFR6.2**: Attribution to ROCKNIX and JELOS MUST be preserved in README and license files.
- **NFR6.3**: All GaoOS-original code MUST be licensed under GPLv2 to match upstream.

---

## 8. User Stories

### US1: Safe OTA Update
**As a** GaoOS user,
**I want to** update my device over WiFi without risk of bricking it,
**so that** I can always get back to a working system even if the update is bad.

**Acceptance criteria**:
- User triggers update from EmulationStation menu or CLI
- Update downloads and writes to inactive slot
- Device reboots into new version
- If new version fails, device automatically reverts to previous version within 3 reboots

### US2: Manual Rollback
**As a** GaoOS user,
**I want to** manually roll back to the previous version if I don't like the new one,
**so that** I have control over which version I'm running.

**Acceptance criteria**:
- `gaoos-update rollback` switches active slot
- Reboot lands on previous version
- All my games, saves, and settings are preserved

### US3: Update Status Visibility
**As a** GaoOS user,
**I want to** see which slot I'm running on and whether an update is pending,
**so that** I understand the state of my system.

**Acceptance criteria**:
- `gaoos-update status` shows clear, human-readable output
- Shows running slot, active slot, both slot statuses, and staged updates

### US4: Power-Safe Update
**As a** GaoOS user,
**I want** the update process to be safe even if my battery dies mid-update,
**so that** I don't lose my device to a failed write.

**Acceptance criteria**:
- Power loss during write only affects the inactive slot
- Running system remains bootable
- User can retry the update on next boot

### US5: Developer Build and Test
**As a** GaoOS developer,
**I want to** build and test the A/B system in a VM before deploying to hardware,
**so that** I can iterate quickly without flashing SD cards.

**Acceptance criteria**:
- QEMU test harness validates full U-Boot boot chain
- Gao-VM test harness validates userspace update logic
- Predefined test scenarios cover all critical paths
- `devenv.nix` provides all build dependencies

### US6: Upstream Sync
**As a** GaoOS maintainer,
**I want to** merge upstream ROCKNIX updates with minimal conflicts,
**so that** we get new emulators and device support without heavy rework.

**Acceptance criteria**:
- Upstream merge produces conflicts in at most one file
- GaoOS-specific code is in additive paths that upstream never touches
- Monthly sync is achievable in under 2 hours

---

## 9. Test Plan

### 9.1 Unit Tests (Gao-VM — fast iteration)

| ID | Scenario | Steps | Expected Result |
|----|----------|-------|-----------------|
| T1 | Normal boot slot A | Set active_slot=a, status=good. Boot. | Boots slot A, status stays good |
| T2 | Normal boot slot B | Set active_slot=b, status=good. Boot. | Boots slot B, status stays good |
| T3 | Pending slot confirms | Set status=pending, retry=0. Boot. Wait 30s. | Status changes to good, retry=0 |
| T4 | Pending slot retries | Set status=pending, retry=0. Kill before confirm. Boot 3x. | retry increments each boot |
| T5 | Max retries rollback | Set status=pending, retry=3. Boot. | Slot marked bad, falls back to other |
| T6 | Bad slot fallback | Set slot_a=bad, active=a. Boot. | Boots slot B instead |
| T7 | Both slots bad | Set both=bad. Boot. | Last resort: boots slot A |
| T8 | Apply to inactive | Run gaoos-update apply. Check state. | Inactive slot written, marked pending, active_slot switched |
| T9 | Manual rollback | Run gaoos-update rollback. Check state. | active_slot switched to other |
| T10 | Status display | Run gaoos-update status. | Shows correct running slot, state, retries |

### 9.2 Integration Tests (QEMU — full boot chain)

| ID | Scenario | Steps | Expected Result |
|----|----------|-------|-----------------|
| T11 | U-Boot slot selection | Compile boot.scr, boot QEMU. | U-Boot reads ab_state.env, selects correct slot |
| T12 | U-Boot retry persistence | Boot pending slot, kill mid-boot, boot again. | retry counter incremented and persisted |
| T13 | U-Boot auto-rollback | Set retry=3, boot. | U-Boot marks bad, switches active_slot, reboots to other |
| T14 | Full update lifecycle | Boot A → apply B → reboot → confirm → reboot. | Runs on B, both slots good |
| T15 | Power loss simulation | Start apply, kill QEMU mid-dd. Boot. | Active slot (A) still boots, inactive (B) corrupt but unused |

### 9.3 Hardware Tests (real device — RK3566 first)

| ID | Scenario | Steps | Expected Result |
|----|----------|-------|-----------------|
| T16 | First boot | Flash GaoOS image, boot RG353. | Boots to EmulationStation, gaoos-update status works |
| T17 | OTA update | Download update, apply, reboot. | New version boots, confirmed good |
| T18 | Rollback on real device | Apply known-bad image, reboot 3x. | Auto-rollback to previous working slot |
| T19 | Storage survival | Update, rollback, update again. Check ROMs/saves. | All user data intact through all slot changes |
| T20 | SD card power pull | Start gaoos-update apply, pull power mid-write. Boot. | Device boots on active slot, no data loss |

---

## 10. Release Milestones

### M1: Fork & Rebrand (Week 1)
- Fork created at Gao-OS/rocknix
- GaoOS distribution config in distributions/GaoOS/
- Branding replaced (splash screens, names, paths)
- Build succeeds for RK3566 (standard 3-partition, no A/B yet)
- Image boots on device or VM

### M2: A/B Boot in VM (Week 2)
- gaoos-ab-boot package added to build
- QEMU test harness passing T1–T10
- Gao-VM test harness passing T1–T10
- gaoos-update CLI fully functional in VM

### M3: A/B Boot on Device (Week 3)
- Image script produces 4-partition layout
- First A/B boot on RK3566 hardware
- Full update lifecycle verified (T14)
- Power-loss scenario tested (T20)
- gaoos-boot-confirm.service working

### M4: CI/CD & Release (Week 4)
- GitHub Actions building per-SoC images
- Release workflow generating update manifests
- Upstream sync dry run completed
- Documentation finalized
- First tagged release: v1.0.0

### Future Milestones

- **M5**: EmulationStation menu integration for update UI
- **M6**: Expand device matrix beyond RK3566
- **M7**: Delta updates (bsdiff patches instead of full images)
- **M8**: Watchdog timer for boot health check
- **M9**: Web dashboard for update server / fleet management

---

## 11. Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| U-Boot env import/export behaves differently across SoCs | Boot fails on some devices | Medium | Test on multiple SoC families early; provide fallback extlinux.conf method |
| Upstream ROCKNIX refactors scripts/image | Merge conflicts in critical path | Medium | Keep A/B logic behind conditional flag; review upstream changes before merging |
| 768MB slots too small for future system images | Cannot apply updates | Low | Monitor image size trend; slot size is configurable in partition script |
| ab_state.env on FAT32 gets corrupted | U-Boot reads garbage | Low | U-Boot defaults to slot A / good on parse failure; FAT32 is journaling-free but writes are atomic at file level |
| Users with existing ROCKNIX installs want to migrate | Destructive repartition required | High | Provide clear migration docs; offer one-click migration tool that backs up storage, repartitions, restores |
| Build system changes break Docker container | CI fails | Low | Pin Docker image version; test container builds separately |

---

## 12. Resolved Decisions

### D1: EmulationStation Integration — Replace the existing update UI

**Decision**: Replace ROCKNIX's built-in update flow entirely rather than running alongside it.

**Rationale**: ROCKNIX's current update flow is tightly coupled to the in-place overwrite mechanism. It lives in the System Settings menu (Start → System Settings → Update), calls shell scripts that download a `.tar` to `/storage/.update/`, and applies on reboot by overwriting the single system partition. Running both flows in parallel would confuse users (two "Update" buttons with different behavior) and create a risk of the old flow accidentally overwriting the active slot.

**Implementation**:
- Replace the ES update scripts (in `packages/sysutils/system-utils/`) with wrappers that call `gaoos-update` commands
- Keep the same menu location: Start → System Settings → Software Update
- Menu items map to:
  - "Check for Updates" → `gaoos-update check` (show version + changelog)
  - "Download & Apply" → `gaoos-update canary` (download, verify, write to inactive slot)
  - "Reboot to Update" → `reboot` (only shown after successful canary)
  - "Rollback to Previous" → `gaoos-update rollback && reboot`
  - "System Status" → `gaoos-update status` (show slot info in a dialog)
- The existing "Change Log" view before update is preserved — fetch it from the update manifest
- Remove the old `/storage/.update/*.tar` detection from the init scripts to prevent accidental in-place overwrites

**Milestone**: M5 (post-launch). For M1–M4, CLI-only is acceptable. ES integration is a polish item.

### D2: Delta Updates — Defer; full images for now

**Decision**: Ship with full SquashFS image replacement. Defer delta updates to M7 or later.

**Rationale**: Full image replacement is the right default for three reasons:

1. **Simplicity**: A/B boot is already a significant change to the update pipeline. Adding bsdiff delta computation, patching logic, and a rebuild step increases complexity and failure modes. Get the core A/B system stable first.

2. **SquashFS is compressed**: A typical ROCKNIX system image is 300–600MB as a SquashFS file. The uncompressed rootfs is much larger, but users only download the compressed image. Bandwidth cost is moderate on WiFi-connected handhelds that update monthly.

3. **Correctness is easier to verify**: With full images, you checksum the whole file before and after writing. With deltas, you must verify the patch result matches the expected target hash, adding another failure point.

**When to revisit**: If system images grow beyond 1GB or users on metered connections report bandwidth as a pain point. At that point, investigate `casync` (content-addressable sync) which chunks the filesystem and only transfers changed chunks — more robust than bsdiff for filesystem images because it operates on content blocks rather than binary diffs.

### D3: Multi-device Update Server — Per-device manifest on GitHub Releases

**Decision**: One JSON manifest per device/SoC, hosted as GitHub Release assets.

**Manifest URL pattern**:
```
https://github.com/Gao-OS/rocknix/releases/latest/download/manifest-{DEVICE}.json
```

Example for RK3566:
```json
{
  "version": "20260401",
  "device": "RK3566",
  "url": "https://github.com/Gao-OS/rocknix/releases/download/v20260401/GaoOS-RK3566.squashfs",
  "sha256": "a1b2c3d4...",
  "size_bytes": 524288000,
  "changelog_url": "https://github.com/Gao-OS/rocknix/releases/tag/v20260401",
  "min_version": "20260301"
}
```

**Rationale**:

- **Per-device manifests** are simpler and safer than a unified manifest. The device knows its own SoC at build time (`gaoos-ab.conf` sets the `DEVICE` variable). No runtime hardware detection logic needed — the correct URL is baked in.
- A unified manifest would require `gaoos-update` to parse a multi-device JSON, match against the running hardware, and handle cases where the device isn't listed. This adds complexity with no benefit.
- **GitHub Releases** is free, has global CDN, and integrates naturally with the CI release workflow. No separate infrastructure to maintain.
- `min_version` field enables skipping versions that require a breaking migration (e.g., partition layout change). `gaoos-update check` can compare against the running version and warn.

**Device detection at build time**: The `DEVICE` variable (RK3326, RK3566, RK3588, etc.) is set during the build and baked into `/etc/gaoos-device`. `gaoos-update check` reads this file to construct the manifest URL.

**Configuration**: The base URL is configurable in `gaoos-ab.conf`:
```ini
UPDATE_MANIFEST_URL=https://github.com/Gao-OS/rocknix/releases/latest/download/manifest-${DEVICE}.json
```
Users running custom builds can point this at their own server.

### D4: Upstream Contribution — Maintain as GaoOS differentiator first, upstream later

**Decision**: Do not attempt to upstream during M1–M4. Revisit after the system is proven stable across multiple devices and release cycles.

**Rationale**:

- **ROCKNIX's update mechanism works for their users**. The in-place overwrite is simpler, well-tested, and the ROCKNIX team may not consider A/B boot worth the added partition overhead and boot script complexity. Pushing an unproven system upstream risks rejection and wasted effort.

- **We need to prove it first**. A/B boot on embedded Linux with diverse U-Boot configurations across 8+ SoC families is nontrivial. We need at least 2–3 release cycles on real devices to discover edge cases (U-Boot env quirks per SoC, SD card write behavior, FAT32 reliability under power loss).

- **The fork structure is designed for eventual upstreaming**. The A/B changes are isolated to `packages/gaoos/`, `distributions/GaoOS/`, and a conditional block in `scripts/image`. If upstream wants it, extracting a clean PR is straightforward.

- **GaoOS benefits from differentiation**. Having a concrete feature that upstream lacks gives GaoOS a reason to exist as a separate distribution. Users who care about update safety choose GaoOS; users who prefer upstream simplicity stay on ROCKNIX.

**When to upstream**: After M4 is complete and GaoOS has been running on real devices for at least 3 months with no A/B-related boot failures. At that point, open a discussion in the ROCKNIX Discord/GitHub to gauge interest before writing a PR.

### D5: Migration Path — Fresh flash only; no in-place repartition

**Decision**: GaoOS requires a fresh SD card flash. No in-place migration from ROCKNIX's 3-partition layout to GaoOS's 4-partition layout.

**Rationale**:

- **In-place repartition is extremely dangerous on embedded devices**. Resizing or moving the storage partition while the system is running risks data loss. Even offline repartition (boot from a second SD card, repartition the first) is error-prone and hard to test across all device variants.

- **The target users don't need migration**. Most handheld gaming users have their ROMs on a second SD card or can re-copy them. The storage partition contains configs, saves, and the writable overlay — saves are the only irreplaceable data, and ROCKNIX already has rclone cloud backup.

- **Detection at first boot adds complexity for a one-time event**. Writing partition detection logic, a repartition wizard, and a backup/restore flow is a significant engineering effort that serves users exactly once. It's not worth it for M1–M4.

**Migration workflow for users**:

```
1. On existing ROCKNIX device:
   a. Back up saves:
      - Enable SSH, scp /storage/saves to computer
      - Or use ROCKNIX's built-in rclone cloud backup
   b. Note any custom configs (RetroArch overrides, WiFi settings)

2. Flash GaoOS image to a NEW SD card (or reuse the same one):
   - Download GaoOS image for your device
   - Flash with Balena Etcher / dd / GaoOS Image Burner

3. First boot:
   - GaoOS boots into EmulationStation
   - Add games to /storage/roms (via Samba, SCP, or second SD card)
   - Restore saves from backup

4. Verify:
   - gaoos-update status shows slot A = good
   - Games and saves work
```

**Documentation**: Provide a clear migration guide in the GaoOS wiki. Include a script `gaoos-migrate-saves.sh` that users can run on their computer to extract saves from a ROCKNIX SD card image and inject them into a GaoOS storage partition.

**Future consideration (M6+)**: If demand is high, consider a "migration image" — a special GaoOS image that, on first boot, detects ROCKNIX's 3-partition layout, backs up `/storage/` to a temporary location on the same SD card, repartitions to 4-partition, restores storage, and reboots. This is risky but technically possible if the SD card has enough free space. Only pursue this if user feedback strongly requests it.

---

## 13. References

- [ROCKNIX Distribution Repository](https://github.com/ROCKNIX/distribution)
- [ROCKNIX Build Guide](https://rocknix.org/contribute/build/)
- [ROCKNIX Package Documentation](https://rocknix.org/contribute/packages/)
- [Android A/B System Updates](https://source.android.com/docs/core/ota/ab)
- [U-Boot Environment Subsystem](https://docs.u-boot.org/en/latest/usage/environment.html)
- [SquashFS Documentation](https://docs.kernel.org/filesystems/squashfs.html)
- [GaoOS A/B Boot Implementation](./README.md)
- [GaoOS Fork Workflow](./FORK_WORKFLOW.md)
