# GaoOS RGB30 boot design

Target: Powkiddy RGB30, Rockchip RK3566, **Generic AArch64** image. The Specific
RK3566 image targets the Powkiddy X55. Host artifact verification does not prove
physical cold boot, display operation, or rollback under power interruption.

## Boot sequence and contracts

```mermaid
sequenceDiagram
    participant ROM as RK3566 Boot ROM
    participant SPL as DDR loader / SPL
    participant UB as Generic U-Boot
    participant FAT as boot FAT (p1)
    participant INIT as Kernel / embedded initramfs
    participant ROOT as SquashFS slot A or B
    participant OS as systemd / GaoOS
    ROM->>SPL: loader at image LBA 64
    SPL->>UB: board ADC detection / matching FIT configuration
    UB->>FAT: boot.scr and gaoos_boot.env
    UB->>FAT: import and validate ab_state.env
    UB->>FAT: persist counted pending attempt
    UB->>INIT: KERNEL + selected RGB30 DTB + slot argument
    INIT->>FAT: mount boot=LABEL=GAOOS at /flash
    INIT->>INIT: canonicalize actual /flash backing device
    INIT->>ROOT: mount p2 (A) or p3 (B), read-only SquashFS
    INIT->>OS: mount storage, move /run, switch_root
    OS->>OS: userspace, autostart and selected frontend health
    OS->>FAT: confirm running slot good; reset retry count
```

| Stage | Required artifact or invariant | Observable failure |
| --- | --- | --- |
| Boot ROM / DDR / SPL | Generic `uboot.bin`, packaged as `Generic_uboot.bin`, embedded at LBA 64 before boot partition | Physical console capture; host byte comparison proves placement only |
| Generic FIT selection | RGB30 board detection matches `rockchip/rk3566-powkiddy-rgb30.dtb` | U-Boot board/configuration output; compiled matcher regression |
| U-Boot script | Valid legacy-image header/data CRC; FAT filesystem read/write commands | `AB:` diagnostics; failed state persistence refuses an uncounted boot |
| Kernel / DTB | AArch64 `Image` with embedded initramfs; flat RGB30 Linux DTB | kernel console; missing or invalid DTB stops the script |
| Initramfs | executable `/init`, BusyBox, ELF loader/libraries and external utilities | console and `/run/initramfs.log` |
| Root selection | valid slot and canonical partition-1 source for mounted `/flash` | stage/device/slot diagnostics; invalid block node or SquashFS fails closed |
| Root handoff | storage mount, kernel overlays and systemd executable | logged fatal A/B failure; production retry reboot or explicit debug shell |
| Userspace health | selected read-only SquashFS, writable EXT4 storage, systemd targets and configured UI | confirmation log and journal; failed confirmation reboots without marking good |

## Confirmed causes and repairs

The baseline `06ea6f13a7c3d96b6aa8c249b34d84ab1dde3d78` invokes `readlink -f`
from `mount_sysroot()` but disables both `CONFIG_READLINK` and
`CONFIG_FEATURE_READLINK_FOLLOW` in the initramfs BusyBox configuration. Host
tests that supplied a `readlink` mock concealed the missing compiled applet.
Both options are enabled. Regressions deny access to host PATH, and artifact
checks inspect and execute the BusyBox actually embedded in `KERNEL`.

Generic U-Boot's FIT descriptions contain a `rockchip/` prefix while the board
model table uses bare Linux DTB names. The matcher now compares the basename.
Changing the table globally would break Linux's flat `device_trees/` paths.
The RGB30 match must succeed instead of using the RG353P default control DTB.
The matcher tests compile the patched C function and distinguish every supported
configuration; final image inspection checks the actual RGB30 Linux DTB too.

Further source-level defects were reproduced: invalid slot arguments selected A
silently; a missing slot block device fell back to legacy `/flash/SYSTEM`; a
failed SquashFS mount could return success after a later optional hook; stderr
was discarded; bad slots could be relabeled good; and exhausted trials could
select the empty factory B partition. These paths now report failure and stop.

Image assembly previously ignored some partitioning, formatting, bootloader and
FAT-copy failures. GaoOS assembly enables checked error propagation and validates
critical inputs before writing the disk image. Artifact inspection also found
that `mformat` automatically selected FAT16 for the boot partition despite the
declared FAT32 layout. GaoOS A/B assembly now explicitly requests FAT32. A
regression checks that request while preserving the upstream formatter command.
The upstream single-slot path
retains its layout. The missing GaoOS `get_fdt_overlays` helper is restored so
boot metadata generation no longer conceals an undefined-command failure.

The recovery path invoked `evtest` without installing it into initramfs. Its
existing package now supplies an initramfs install and dependency. `mkfifo` is
enabled for logging. GaoOS RK3566 production command lines omit the inherited
`systemd.debug_shell=ttyS2`; recovery shells require explicit boot options.

## Partition and device selection

The RK3566 image has a protective MBR and primary/backup GPT:

| Partition | GPT name | Filesystem / factory content | Size |
| --- | --- | --- | --- |
| p1 | `boot` | FAT32, label `GAOOS`; KERNEL, DTBs, boot.scr, metadata | 256 MiB |
| p2 | `slot_a` | complete raw SquashFS image | 1536 MiB |
| p3 | `slot_b` | zero-filled; intentionally empty until an OTA write | 1536 MiB |
| p4 | `storage` | EXT4, label `STORAGE`; configuration, saves and logs | 32 MiB initially; existing resize flow expands it |

The first partition begins at sector 32768. Bootloader data begins at sector 64.
No loop device, physical-disk write, or live mount is required for host image
inspection.

`gaoos.slot=a` selects p2 and `gaoos.slot=b` selects p3. Initramfs finds the source
actually mounted at `/flash`, resolves symlinks with the target BusyBox
`readlink -f`, and derives that disk's slot partition. It validates MMC indexes
and supported block-device forms. It does not trust U-Boot's `gaoos.dev` hint:
Linux MMC numbering may differ. Unknown paths, missing nodes, malformed slots,
and failed SquashFS mounts never invoke the legacy rootfs fallback.

The packaged init also embeds the GaoOS A/B build flag. Missing all GaoOS
command-line arguments therefore cannot silently select upstream shell or
legacy-root behavior on a production A/B image.

`boot=LABEL=GAOOS` and `disk=LABEL=STORAGE` retain upstream discovery behavior.
Multiple attached cards with identical labels remain ambiguous; validate with
one test card and remove duplicates. This change does not invent a mapping
between U-Boot device numbers and Linux device numbers.

## State, retries and confirmation

U-Boot imports only the six state fields, after clearing old values. It accepts
slots A/B, statuses good/pending/bad/empty, counters 0–3, and the three-attempt
policy. Missing or malformed state recovers deterministically to a counted A
trial with B empty. This does not claim that A contains a valid filesystem.

Before each kernel launch, including a formerly good slot, the chosen slot is
marked pending and its attempt count is persisted. Three unconfirmed launches
exhaust that slot; the next boot marks it bad and selects a usable alternate.
The alternate is counted too. No usable alternate leaves an explicit recovery
failure; the bootloader does not promote bad or empty slots to good.

The factory B partition is deliberately empty. Missing/corrupted A cannot be
recovered from B until a verified update has populated B. Reflash or repair from
explicit recovery mode in that case. Sharing KERNEL and DTBs on FAT remains a
limitation of the existing OTA design: slot rollback is rootfs rollback, not an
independent rollback of every boot artifact.

U-Boot omits empty variables from text exports. Userspace accepts omitted status
fields as empty while requiring and validating all four core fields. Metadata
is parsed as data rather than executed as a shell script. Userspace state writes
flush a complete temporary file before rename and preserve previous state on
write/flush/rename failure. FAT state updates and physical power-loss behavior
still require device qualification.

Boot confirmation runs after userspace autostart and a stabilization delay. It
requires the running slot argument, its actual read-only SquashFS root on the
`/flash` disk, writable EXT4 storage with a successful temporary-file probe,
minimum uptime, active systemd targets/autostart, and every configured frontend
service when games startup was requested. RGB30 normally uses `sway.service`
and `essway.service`. Manual `gaoos-update confirm` uses the same health gate.
Failure leaves the slot pending and the systemd unit requests reboot. Kernel
hangs, power-loss recovery and watchdog timing are hardware test requirements.

## Embedded dependencies and verification boundary

BusyBox ash uses standalone applet dispatch; most applets need no filesystem
symlink. Required capabilities include `readlink -f`, mount/umount, filesystem
and text utilities, FIFO/tee logging, switch_root, reboot and explicit debug
shell helpers. Non-BusyBox dependencies include libc/loader, fsck tools, blkid,
AVFS, splash and evtest. The runtime regression maintains the concrete audited
list; optional network boot and unrelated device families are outside RGB30
qualification.

Linux embeds initramfs in `KERNEL`. A SquashFS-only rebuild cannot repair its
commands or `/init`. Build-time validation extracts the actual embedded cpio,
checks compiled applets, and compares its init and BusyBox with staged outputs.
Final validation compares the FAT kernel with that verified kernel and reads all
SquashFS contents. QEMU user mode executes target userspace binaries; it does
not emulate an RGB30 cold boot, Boot ROM, display, MMC, or power management.

See [debugging](../debugging/rgb30-boot.md) and
[validation](../testing/rgb30-boot-validation.md) for procedures and evidence.
