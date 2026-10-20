# Debugging GaoOS RGB30 boot

The RGB30 uses the **RK3566 Generic AArch64** image. The Specific image targets
the Powkiddy X55. A completed build, a valid disk image, or QEMU execution of
BusyBox does not establish that an RGB30 cold-boots. Record physical testing as
`HARDWARE_BOOT_UNVERIFIED` until it has been performed on a real RGB30.

## Enable diagnostics on a removable test card

Back up `gaoos_boot.env` and `ab_state.env` from the FAT boot partition before
editing them. Preserve the existing device-tree settings. In `gaoos_boot.env`,
replace only the `extra_cmdline=` line with:

```text
extra_cmdline=console=ttyS2,1500000 console=tty0 loglevel=7 ignore_loglevel progress debugging
```

Removing `quiet` permits kernel messages. `console=tty0` last directs the primary
console to the local display when its framebuffer becomes available. This cannot
display messages before the display driver initializes. `console=ttyS2,1500000`
matches the configured serial console, but physical access to that UART has not
been verified. It does not create a USB serial connection.

The initramfs options are:

| Option | Behavior |
| --- | --- |
| `progress` | Prints boot progress and enables systemd status messages. |
| `debugging` | Enables additional initramfs diagnostics and a local root shell on errors. |
| `break=mount_flash` | Opens a local shell after mounting `/flash`, then after subsequent stages. |
| `break=mount_sysroot` | Stops after mounting the selected root; errors also permit a shell. |
| `break=all` | Opens a shell after every boot stage. |
| `recovery` | Requests the existing USB mass-storage recovery mode; it does not provide a shell. |

For a failure before root mounting, add `break=mount_flash`. A breakpoint alone
opts into an error shell even without `debugging`. Type `exit` to continue after
a successful stage; subsequent stages also stop. After a fatal A/B error, exiting
the shell leaves boot halted instead of continuing with an invalid root.

These options require a usable console and an input device. A local USB keyboard
depends on working USB host support and the correct port. Remove `debugging` and
`break=` when returning the card to production. Do not add
`systemd.debug_shell=...` to production command lines. Normal GaoOS A/B failure
handling reports the error and reboots after ten seconds so the bootloader can
count retries and roll back; it does not automatically offer a root shell.

## Where the evidence lives

Initramfs stdout and stderr are copied to the console and `/run/initramfs.log`.
Boot-stage markers, the mounted boot device, the selected slot and the root mount
failure are reported even without `progress`. A fatal failure also prints the
mount table and the last 80 kernel log lines.

`/run` survives the transition to systemd. Once writable storage is mounted, the
log is copied to `/storage/init.log`, including before the final handoff or a
fatal reboot when storage is reachable. This is a snapshot of the current boot,
not a rotating history. Preserve it before another boot overwrites it.

When `/storage` cannot mount, `/run/initramfs.log` and console output are the
available evidence. RAM logs disappear on reset or power loss. In an explicitly
enabled debug shell, inspect or copy them before rebooting:

```sh
cat /proc/cmdline
cat /proc/mounts
cat /run/initramfs.log
dmesg
ls -l /dev/mmcblk*
```

Copy logs only to a filesystem that is actually mounted and writable. The FAT
boot partition is normally mounted read-only, and writing logs there requires
an intentional remount. Do not assume an unmounted `/storage` directory is
persistent storage.

## Diagnose by boot stage

| Last evidence | Checks and likely boundary |
| --- | --- |
| Black screen, no captured output | Check power, boot-card seating, image checksum and full-image flashing. A black display alone cannot distinguish Boot ROM, SPL, kernel, or display initialization failure. |
| No U-Boot banner on a verified serial connection | Inspect bootloader placement and Generic SPL/FIT contents. Kernel or rootfs changes cannot repair a failure before U-Boot. |
| U-Boot starts, wrong board or DTB selected | Verify the Generic FIT includes RGB30 and selects its configuration. Check `fdtfile`, not merely the default FIT configuration. |
| `GaoOS A/B Boot` or `AB:` errors | Check `boot.scr`, `gaoos_boot.env`, `ab_state.env`, FAT integrity, kernel loading and DTB loading. |
| Kernel output, no initramfs stage markers | Inspect the actual `KERNEL` embedded initramfs, ELF loader/libraries, executable `/init`, and BusyBox. |
| `mount_flash` fails | Check `boot=LABEL=GAOOS`, the FAT partition, duplicate labels on other attached cards and kernel MMC support. The post-flash hook has not run yet. |
| `mount_sysroot` fails | Check the detected `/flash` backing device, canonical path, slot argument, partition table and raw SquashFS payload. |
| `mount_storage` or `prepare_sysroot` fails | Inspect storage EXT4, writable state, overlay setup and the staged root's systemd executable. |
| systemd starts, userspace fails | Inspect `journalctl -b`, `systemctl --failed`, `/run/initramfs.log`, `/storage/init.log`, and boot-confirmation service status. |

### U-Boot and the RGB30 device tree

The Generic SPL/FIT device-tree descriptions may include
`rockchip/rk3566-powkiddy-rgb30.dtb`. The board matcher must compare the basename
correctly. Linux boot files use the flat path
`device_trees/rk3566-powkiddy-rgb30.dtb`; adding `rockchip/` globally breaks that
path. Generic metadata leaves `gaoos_fdt` empty and sets
`gaoos_fdtdir=device_trees` so `boot.scr` uses U-Boot's board detection.

If an interactive U-Boot console is available, capture rather than change its
configuration first:

```text
version
printenv devtype devnum fdtfile bootargs
```

Capture `AB: slot ... attempt ...`, `AB: state persisted`, kernel-load and DTB-load
messages. Missing or invalid state invokes counted slot-A recovery. No usable
slot, failed state persistence, missing boot metadata, missing kernel or missing
DTB stops the script. A factory image leaves slot B empty; manually selecting it
does not create a second valid root filesystem.

### Initramfs and A/B mounts

`gaoos.slot=a` selects partition 2; `gaoos.slot=b` selects partition 3. Initramfs
derives the parent disk from the actual `/flash` mount and canonicalizes it with
`readlink -f`. U-Boot's `gaoos.dev` is a hint, not authority for Linux MMC
numbering. `/dev/mmcblk0p1` and `/dev/mmcblk1p1` may therefore both be valid boot
devices, depending on which card Linux mounted.

The original failure disabled the required BusyBox `readlink` applet and its
canonicalization feature. Check the BusyBox **extracted from the generated
kernel**, including its loader and libraries. A development-host `readlink` or a
mocked command does not validate the target initramfs. Use the artifact validator
described in [boot validation](../testing/rgb30-boot-validation.md), optionally
with AArch64 user-mode QEMU. User-mode QEMU verifies target utility execution; it
does not emulate the RGB30 Boot ROM, SPL, display, MMC controller or power loss.

In a debug shell, an expected root selection looks like:

```text
initramfs [mount_sysroot]: Boot device /dev/mmcblk0p1; mounting slot a from /dev/mmcblk0p2
```

An absent partition, malformed slot argument or SquashFS mount error must stop
normal boot. Do not mark a broken root good by manually running confirmation.
Inspect `gaoos-update status` and the confirmation service after reaching normal
userspace; successful mounting alone is insufficient health evidence.

## Available debugging interfaces

These are source/configuration findings, not hardware qualification:

| Interface | Repository evidence and limits |
| --- | --- |
| Built-in panel | RGB30 DTB declares `powkiddy,rgb30-panel`. Kernel configuration includes Rockchip DRM, ST7703 panel and framebuffer console support. Console output still depends on the correct DTB and successful display initialization. |
| HDMI | RGB30 includes the RK2023 device-tree base, which enables HDMI and its connector. `CONFIG_ROCKCHIP_DW_HDMI=y` and framebuffer console support are enabled. Attach HDMI before boot and test it, but do not assume it displays SPL or the earliest kernel messages. |
| UART | Device tree chooses `serial2:1500000n8`, enables `uart2`, and the kernel enables the 8250/DW console. Accessible pads, electrical levels and a tested connection are unverified. Do not assume a user-accessible serial port exists. |
| USB mass storage | Device tree enables a peripheral-mode USB controller; kernel enables configfs and mass storage. Existing recovery code can export MMC devices. Enumeration, port/cable behavior and physical button detection require RGB30 testing. |
| USB serial shell | `CONFIG_USB_CONFIGFS_SERIAL`, `CONFIG_USB_CONFIGFS_ACM` and `CONFIG_USB_G_SERIAL` are disabled. There is no implemented early-boot USB serial shell. |

No network shell is started by these initramfs debugging options. SSH becomes
relevant only after the normal root filesystem and its services start.

## Recovery mode and hooks

The existing recovery entry is the `recovery` kernel option or a detected
Volume Down key. It runs before mounting the boot/root/storage filesystems and
exports detected MMC devices as USB mass storage. It needs a functioning UDC,
configfs, gadget mass-storage support and, for buttons, packaged `evtest` and
working input nodes. Host-side checks cannot prove that a particular RGB30 port
enumerates. The kernel/configuration does not establish USB serial support.

Recovery exports whole MMC devices with write access. Back up the card before
repairing it, avoid simultaneous host/device mounts, eject host filesystems
cleanly, then use Volume Up to request reboot. If gadget recovery does not
enumerate, remove the card and inspect it on a host instead. Neither recovery
entry point can help when the bootloader or kernel never starts.

`/flash/post-flash.sh` is an existing root hook sourced **after** a successful
read-only mount of `/flash`. It can collect later evidence or adjust subsequent
initramfs behavior on a trusted diagnostic card. It cannot repair missing
BusyBox applets needed before that point, a failed `/flash` mount, SPL/FIT board
detection or a kernel that never started. Hook errors now propagate into A/B
failure handling. Keep it absent from normal cards unless deliberately needed:
whoever can alter the boot card can alter this privileged hook and boot metadata.

Changes to initramfs utilities, init scripts or logging require rebuilding the
embedded initramfs and `KERNEL`, then regenerating the image. Replacing the
SquashFS payload alone leaves early boot unchanged. See the
[boot design](../boot/rgb30-boot-design.md) and
[validation checklist](../testing/rgb30-boot-validation.md).
