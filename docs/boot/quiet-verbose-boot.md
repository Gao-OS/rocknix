# GaoOS RGB30 quiet and verbose boot

GaoOS boots quietly by default. The initramfs draws the existing GaoOS logo in
the center of the framebuffer, with its aspect ratio preserved. Routine
initramfs messages are recorded instead of printed. systemd displays failed
units, while successful unit startup messages and the version banner stay in
the journal.

Hold **START before switching on the RGB30**, and keep it held through early
Linux startup, to request verbose boot. The initramfs samples the onboard
`BTN_START` state (315) for at least 500 ms. A release or query failure cancels
the request. Other buttons and external controllers do not activate it.

For diagnosis without button input, append `gaoos.verbose=1` to `extra_cmdline`
in the boot partition's `gaoos_boot.env`. Preserve its existing parameters and
the other metadata fields. Remove the parameter to restore normal behavior.
This override works on other GaoOS hardware too; automatic button detection is
restricted to the RGB30.

Verbose boot replays the buffered initramfs output, prints the available kernel
ring buffer, raises the kernel console log level, enables initramfs progress
and systemd status, and skips the graphical splash. It does not change `DEBUG`,
request `break=`, start `debug_shell`, or enable systemd's debugging shell.
The pre-existing explicit `debugging`, `progress`, `break=`, and `recovery`
options retain their separate meanings.

Detection starts in Linux initramfs. It cannot retroactively change SPL or
U-Boot output. Some kernel activity precedes detection; `dmesg` replays only
what remains available in the kernel ring buffer.

## Input initialization

The RGB30 DT inherits its joypad configuration from RK2023:

- START is GPIO3_PB5, active low, code 315.
- The DT compatible string must include exactly `powkiddy,rgb30`.
- The input device must have platform ancestry
  `/sys/devices/platform/rocknix-singleadc-joypad/input/inputN`, name
  `retrogame_joypad`, and physical path `rocknix-singleadc-joypad/input0`.

The driver reports a USB input bus ID even for the onboard GPIO/ADC controller,
so matching the bus ID alone is insufficient. The detector checks platform
ancestry and identity, then queries only that event node with `evtest --query`.
Recovery's existing `is_key_pressed()` helper also accepts this exact-device
argument. A one-second BusyBox timeout bounds each query; the first query
error or timeout ends detection.

For GaoOS RK3566, the build copies the pinned `rocknix-singleadc-joypad` source
and header from the existing `rocknix-joypad` package into the kernel and links
it in. GPIO, IIO/ADC, input, evdev, and joystick support are already built in to
the RK3566 configuration. This avoids depending on systemd, InputPlumber, or
a module that is unavailable before the root filesystem is mounted. It also
avoids a kernel/initramfs/module build dependency cycle. The other joypad
driver remains packaged as a module; the built-in driver is not duplicated.
Other distributions keep their existing module packaging.

Discovery waits at most twenty 50 ms intervals (one second) for the exact
onboard device. An available, released START returns immediately. A held START
requires eleven samples separated by ten 50 ms intervals. Missing devices
return to normal boot after the bounded discovery period. The query timeout
adds at most one second before failure, on top of any discovery and completed
hold intervals; the detector never retries after a query failure.

## Logging and failures

`start_boot_logging()` redirects stdout, stderr, and diagnostic fd 3 directly
to `/run/initramfs.log` during quiet boot. No background logger or FIFO exists
in this mode. `progress()` and `debug_msg()` still record diagnostics.

`enable_boot_console()` clears the framebuffer console and replays the existing
log once. It starts a FIFO and `tee -a` for subsequent log and console output.
Re-entering this function does not replay data or start another process.
`stop_boot_logging()` closes every pipe writer before waiting for `tee` to drain.
The FIFO pathname is removed after its reader and writer have opened it.

Fatal errors reveal the console even during quiet boot. GaoOS A/B failures keep
their existing ten-second delay, reboot, counted retries, and rollback policy.
START cannot open the privileged failure shell; only existing explicit debugging
or break options authorize that path. Filesystem-repair prompts also reveal
the console so an interactive decision is not hidden behind the logo.

The `/run` mount moves into the real root, preserving `initramfs.log` for
systemd. A copy is saved as `/storage/init.log` once storage is available and
after draining the logger before `switch_root`. If storage cannot mount,
the RAM log and visible failure output remain available until reboot; RAM
contents cannot survive that reboot. Kernel messages remain available through
`dmesg` and the system journal, subject to their normal buffer retention.

Quiet mode passes `--show-status=error` to systemd. Runtime drop-ins redirect
the explicitly console-writing `show-version` and `userconfig` services to the
journal. Verbose mode passes `--show-status=yes` and retains their defaults.

## Splash and applet packaging

`gaoos-splash:init` replaces `rocknix-splash:init` only for GaoOS. It compiles
the pinned upstream framebuffer and DT rotation support with a small GaoOS
renderer and an RGB header generated from
`distributions/GaoOS/logos/gaoos-logo.png`. The runtime needs libc only: no PNG,
SVG, window system, or GPU userspace library is added to initramfs. Upstream
license and attribution remain in the source package. The logo fits within
80 percent of the logical display dimensions, centered over a matching dark
background, with 0/90/180/270 degree rotation and 16/32-bit framebuffer support.
The caller waits up to one second for framebuffer registration, returning
immediately if it is ready. An unavailable framebuffer is logged without
preventing the system from booting.

The former configuration compiled `mountpoint` but disabled applet symlink
installation. The initramfs now installs BusyBox applet symlinks, and init's
mountpoint checks explicitly use `/usr/bin/busybox mountpoint`. The runtime
checker executes the packaged binary and verifies command lookup and symlinks,
rather than inferring executable availability from `CONFIG_MOUNTPOINT=y`.

## Debugging and verification

After a verbose boot, inspect:

```sh
cat /proc/cmdline
cat /sys/firmware/devicetree/base/compatible | tr '\000' '\n'
cat /proc/bus/input/devices
cat /sys/class/input/event*/device/name
cat /sys/class/input/event*/device/phys
readlink -f /sys/class/input/event*/device
cat /run/initramfs.log
cat /storage/init.log
dmesg
journalctl -b
```

Find the event node with the identity above. `evtest --query /dev/input/eventN
EV_KEY 315` exits **10 when pressed**, **0 when released**, and nonzero on
other errors. Input device numbering is not fixed. Failure to discover this
device should be investigated in the DT, built-in kernel driver, or its probe
logs, rather than by changing detection to an unrelated button.

Scoped regressions live in `packages/gaoos/gaoos-ab-boot/tests/`, with rendering
tests in `packages/gaoos/gaoos-splash/tests/test-splash.py`. Run the input and
quiet/verbose tests, existing A/B boot tests, then execute the actual staged
initramfs checker with an AArch64 QEMU user emulator:

```sh
python3 packages/gaoos/gaoos-ab-boot/tests/test-quiet-verbose-input.py
python3 packages/gaoos/gaoos-ab-boot/tests/test-quiet-verbose-boot.py
python3 packages/gaoos/gaoos-ab-boot/tests/test-initramfs-runtime.py \
  build.GaoOS-RK3566.aarch64/initramfs --qemu /path/to/qemu-aarch64-static
```

Build a complete image with the documented project environment:

```sh
DEVICE_ROOT=RK3566 DISTRO=GaoOS PROJECT=ROCKNIX DEVICE=RK3566 ARCH=aarch64 \
  ./scripts/build_distro
```

Do not qualify this change from SquashFS alone. Validate the rebuilt KERNEL's
embedded initramfs using `scripts/gaoos-validate-boot.py --quiet-boot
--initramfs <staged-directory>`, compare the FAT KERNEL with that artifact, and
validate the factory Generic image with `scripts/gaoos-validate-image.py`.
Inspect the final kernel's built-in joypad symbols and configuration as well.

The existing final `exec switch_root` boundary is unchanged. Its fatal exec
errors are visible on the console and the kernel's configured panic timeout
reboots PID 1 failure. Since the initramfs logger is drained before this exec,
an error emitted by `switch_root` itself is not appended to the persisted
initramfs log. The preceding boot stages remain recorded.

Host tests and artifact inspection do not establish physical boot acceptance.
A release remains **HARDWARE_BOOT_UNVERIFIED** until a real RGB30 demonstrates
normal quiet boot, START-held verbose boot, short/wrong-button rejection,
fatal error visibility, and unchanged counted A/B retry/rollback behavior.
