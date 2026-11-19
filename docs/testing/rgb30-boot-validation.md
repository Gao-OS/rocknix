# RGB30 boot validation

Qualification target: GaoOS RK3566 Generic AArch64 on Powkiddy RGB30.
**HARDWARE_BOOT_UNVERIFIED** is the required status until physical cold-boot,
reboot, slot-switch, failed-boot and rollback tests have been recorded.

## Reproducible host checks

Use `devenv shell`, or the repository Docker build environment. The qualified
local Docker image and exact invocation are recorded with the validation report.
Use `DISTRO=GaoOS PROJECT=ROCKNIX DEVICE_ROOT=RK3566 DEVICE=RK3566 ARCH=aarch64`.
Do not use `PROJECT=GaoOS`, select the X55 Specific image for RGB30, or infer a
boot result from a successful SquashFS build.

Run the package-scoped regressions from the repository root:

```bash
export PYTHONDONTWRITEBYTECODE=1
for test in packages/gaoos/gaoos-ab-boot/tests/test-*.py; do
  case "$test" in *test-initramfs-runtime.py) continue ;; esac
  python3 "$test" || exit
done
bash packages/gaoos/gaoos-ab-boot/tests/test-update-safety.sh
bash packages/gaoos/gaoos-ab-boot/tests/test-fr9-integration.sh
```

The Python tests cover compiled RGB30 FIT matching, required applets, canonical
MMC paths and symlinks, alternate supported disk names, invalid/missing slot
arguments, root mount failure, missing nodes, failure propagation from hooks,
logging and shell gating, retry exhaustion and rollback, omitted U-Boot empty
variables, corrupt state, boot-artifact CRC/consistency, malformed GPT, image
assembly fault injection, and workflow publication contracts. The updater tests
include checksum/write/flush/rename failures and the shared confirmation health
gate. Their real SquashFS/delta test requires host `mksquashfs`, `unsquashfs`,
`bsdiff` and `bspatch`; a missing-tool skip is not a pass for that integration.

Before a full build, preserve old artifacts: `scripts/build_distro` normally
prunes matching files from `target/` and `release/`. Clear the scoped Linux and
BusyBox init build stamps, or clean those packages using the supported scripts,
so the kernel actually embeds the new initramfs. Do not delete unrelated build
caches or packages to conceal a failure.

```bash
DEVICE_ROOT=RK3566 DISTRO=GaoOS PROJECT=ROCKNIX DEVICE=RK3566 ARCH=aarch64 \
  ./scripts/build_distro
```

The build must regenerate BusyBox/initramfs and `KERNEL`, not merely rootfs.
`scripts/image` extracts the kernel's actual embedded archive and compares init
and BusyBox bytes with staged files. `scripts/mkimage` validates critical boot
inputs, SquashFS header and capacity, and fails on partition, formatting, FAT
copy or bootloader-write errors. The selected project package recipe must build
host `unsquashfs`; editing the overridden base recipe is insufficient.

## Execute packaged initramfs commands

An AArch64 user-mode emulator can execute the target BusyBox with the packaged
ELF loader and libraries. It cannot establish kernel, Boot ROM, DDR, DTB board
detection, display, MMC or watchdog correctness on RGB30.

```bash
python3 packages/gaoos/gaoos-ab-boot/tests/test-initramfs-runtime.py \
  build.GaoOS-RK3566.aarch64/initramfs --qemu /path/to/qemu-aarch64-static

python3 scripts/gaoos-validate-boot.py \
  --kernel build.GaoOS-RK3566.aarch64/build/linux-7.2.7/arch/arm64/boot/Image \
  --init build.GaoOS-RK3566.aarch64/initramfs/init \
  --busybox build.GaoOS-RK3566.aarch64/initramfs/usr/bin/busybox \
  --qemu /path/to/qemu-aarch64-static --report /tmp/rgb30-kernel.json
```

These checks use target applet enumeration and `readlink -f` with host PATH
disabled. BusyBox imports `readlink(2)` even when its command is disabled, so an
ELF symbol/string search alone is insufficient. Applet-name table validation
and actual execution are separate checks. Logging must drain without leaving an
inherited debug descriptor holding its FIFO open.

Without binfmt registration, QEMU user mode resolves `/proc/self/exe` to the
emulator when standalone ash re-executes a forked applet. The logging test uses
explicit QEMU wrappers for `mkfifo`, `tee` and `rm`, each executing the same
packaged BusyBox binary. These are execution adapters; they do not substitute
host utilities. Native standalone dispatch for those forked applets still needs
kernel or hardware execution evidence.

## Inspect the complete generated image

Run this with host `lzop`, `mtools`, `dosfstools`, `e2fsprogs`, `dtc/fdtget`, and
`unsquashfs` available. Use the actual output names and matching build artifacts:

```bash
python3 scripts/gaoos-validate-image.py \
  --image target/GaoOS-RK3566.aarch64-20261020-Generic.img.gz \
  --kernel build.GaoOS-RK3566.aarch64/build/linux-7.2.7/arch/arm64/boot/Image \
  --uboot build.GaoOS-RK3566.aarch64/build/u-boot-Generic-v2026.01/uboot.bin \
  --system target/GaoOS-RK3566.aarch64-20261020.squashfs \
  --init build.GaoOS-RK3566.aarch64/initramfs/init \
  --busybox build.GaoOS-RK3566.aarch64/initramfs/usr/bin/busybox \
  --report /tmp/rgb30-validation/image.json
```

The verifier reads inputs without modification and checks filesystem copies in a
temporary directory. Required checks are:

| Check | Evidence |
| --- | --- |
| Compressed image | complete gzip decoding and CRC, bounded expected image size |
| Partition table | protective MBR, primary/backup GPT headers and entry CRCs, GUIDs, ordered non-overlapping extents and exact labels/sizes |
| Bootloader | exact intended Generic binary at LBA 64, before the first partition |
| FAT | filesystem check, label/size, extracted KERNEL/script/DTB/metadata |
| Device tree | RGB30 model, `powkiddy,rgb30` and `rockchip,rk3566` compatibles; Generic board-selected path |
| Kernel | FAT kernel equals rebuilt Image; embedded initramfs bytes and target applets |
| Scripts/state | boot.scr CRC and exact source, approved factory state, no legacy SYSTEM/extlinux root fallback, no production debug shell |
| Slots | A payload equals raw SquashFS, zero padding, intentionally empty zero-filled factory B |
| Root filesystem | complete extraction/read, GaoOS/RK3566/AArch64 identity, required systemd and matching boot/update files |
| Storage | EXT4 magic/label and read-only e2fsck of the extracted partition |

Store command results, image/kernel/BusyBox/bootloader hashes, source commit and
source manifest together. If a build occurred with dirty source, record the
exact source file hashes/diff and rebuild publication artifacts from the final
commit. An old successful report is not evidence for newly changed artifacts.

## CI qualification

The scheduled run
[39361932439](https://github.com/Gao-OS/rocknix/actions/runs/39361932439)
failed before creating any jobs. Its annotation states that nested
`build-aarch64-toolchain` requested `actions: write` but the caller allowed
`actions: none`. This is a repository workflow permission failure, independent
of RGB30 hardware boot. The caller and nested jobs now grant the required
permissions explicitly; publication permissions are scoped to those jobs.

Validate changed workflows with `actionlint` and the package release regressions.
After pushing, inspect the run's actual jobs/conclusion. Workflow parsing success
does not prove hosted build completion; quota, runner, registry or infrastructure
failures must retain their actual status. Stable publication remains a separate
physical-hardware gate. The legacy nightly and official workflows create drafts.

### Signed OTA candidates

Run the **Build** workflow from `Gao-OS/rocknix@gaoos` with **RK3566** selected.
Choose `ota_channel: validate-only` to produce a qualified image, raw SquashFS,
and validation report for physical testing. This mode requires no signing
environment and leaves the candidate with the workflow artifacts. Choose
`nightly`, `beta`, or `stable` to also sign and register it. The build uses the
workflow run number as the immutable firmware sequence. The image validator
compares the whole factory image and raw SquashFS from the same build, checks
the rootfs sequence, source commit, and loaded kernel module release, and
records the exact kernel and RGB30 DTB hashes. Its report archive retains the validation commands and
`HARDWARE_BOOT_UNVERIFIED` status.

Configure the GitHub `ota-signing` environment for the `gaoos` branch and
required reviewers. Supply these environment settings before registration.

| Setting | Purpose |
| --- | --- |
| Secret `GAOOS_OTA_SIGNING_KEY` | Ed25519 PKCS#8 PEM held by the signing step |
| Secret `GAOOS_OTA_PUBLICATION_TOKEN` | OTA registry ingestion credential |
| Secrets `GAOOS_OTA_S3_ACCESS_KEY_ID` and `GAOOS_OTA_S3_SECRET_ACCESS_KEY` | Restricted S3 writer and checksum reader |
| Variables `GAOOS_OTA_S3_ENDPOINT`, `GAOOS_OTA_S3_BUCKET`, and `GAOOS_OTA_S3_REGION` | S3 API origin, immutable bucket, and signing region |
| Variable `GAOOS_OTA_API_URL` | HTTPS control-plane origin |

The signing job runs after the verified build and archives the signed envelope.
Storage must support conditional single-object creation and checksum-enabled
HEAD requests. The job verifies the stored checksum and length before
registering the candidate. The operator controls staging and promotion after
acceptance.

Two builds are compatible for a rootfs-only upgrade when the installed boot
layout, embedded initramfs and kernel bytes, and RGB30 DTB bytes match the
candidate manifest. Record matching hashes before attempting the physical
upgrade. A change to these shared components requires an independently
qualified full image to establish a new boot baseline.

### Derive same-boot qualification candidates

Use a complete current factory image from a clean source checkout. For a
fresh local build use `GAOOS_OTA_SEQUENCE=0`. Run the full image validator
above with `--ota-sequence 0 --ota-commit "$(git rev-parse HEAD)"` and keep
its report. For an existing positive-sequence build use the embedded sequence
and the report from that build. The factory image must already contain the
signed OTA client and event reporter.

```bash
python3 scripts/gaoos-qualify-ota.py \
  --image target/GaoOS-RK3566.aarch64-DATE-Generic.img.gz \
  --report /tmp/rgb30-validation/image.json \
  --include-failed-boot \
  --output /tmp/rgb30-ota-qualification
python3 scripts/test-gaoos-qualify-ota.py
```

The helper binds its input to the validated factory hash and rechecks the GPT,
slot A, kernel, and DTB. It derives two healthy SquashFS candidates and, with
the flag shown above, one failed-boot candidate. Each receives a distinct
version, sequence and test marker. The rootfs programs and kernel modules keep
their original bytes. Leave enough
temporary space for the decompressed disk and SquashFS data. Use SquashFS tools
with pseudo-file reconstruction support. [SquashFS pseudo-file format](https://github.com/plougher/squashfs-tools/blob/master/Documentation/4.7.5/USAGE-UNSQUASHFS.md#7-unsquashfs-pseudo-file-output)

Reports for the derived payloads carry `TEST_ONLY` and the original image,
rootfs, and source commit hashes. The optional `.qa-fail` image masks
`rocknix-autostart.service` only in the trial root filesystem. The unchanged
boot confirmation service expects autostart to be active, so the trial fails
confirmation while the confirmed peer retains its original services. The GitHub
signing job accepts complete build validation reports. For qualification,
provision an isolated OTA server,
bucket, test device and signing key. Use `--allow-qualification` on
`.github/scripts/sign-ota-release.py` with `--channel nightly` and on
`.github/scripts/register-ota-release.sh` with that deployment's credentials.
Retain candidate 1's bytes as the recovery target. After confirming candidate 2
and revoking it on the test server, sign a fresh envelope for the original
candidate 1 with `--rollback-from` set to candidate 2's sequence. Use the
same exact payload when registering the recovery envelope.

## Physical RGB30 acceptance

Use a spare card and preserve existing saves/configuration. Flash the **whole
Generic image**, verify its checksum and safely eject it. Do not write a raw
SquashFS payload to the whole card. A factory B slot has no rootfs until updated.

Record image SHA256, source commit, card/device identity, logs and result for:

1. Complete power-off and cold boot into the frontend on A; confirm pending →
   good and retry reset only after the health gate passes.
2. Warm reboot and repeated cold boot, with local display/input and persistent
   storage working. Preserve `/storage/init.log` and confirmation journal.
3. Provision the RGB30 with an assigned test channel and the trusted public
   signing keys. Record the running and offered sequences, exact boot-component
   hashes, the `gaoos.fdt` argument in `/proc/cmdline`, and successful authenticated discovery. From EmulationStation or the
   CLI, stage the compatible signed OTA. Verify that the running slot stays
   mounted and unchanged, B is pending, and the device has yet to reboot.
4. Reboot manually into B. Confirm that it reaches the frontend and becomes good
   after the health checks. Verify that the server receives the deployment and
   boot confirmation for the device and target sequence. If the server rejects
   an event permanently, inspect `/storage/.update/.ota/rejected-events` on the
   device. Temporary delivery failures stay queued for retry.
5. Test explicit local rollback while both slots are confirmed good. Cold boot
   and verify the health state and original saves and configuration.
6. Revoke a test release and verify that new discovery stops offering it.
   Separately exercise the signed recovery path from an explicitly authorized
   current sequence to a previously promoted boot-compatible recovery target.
7. With a confirmed source slot, stage the signed `.qa-fail` image on the
   disposable card. Pause that offer after staging, then reboot explicitly.
   Capture three failed confirmations and the next boot into the confirmed
   source. Record the bad status and persisted retry count for the trial slot,
   the server's rollback event for the same attempt, and the unchanged test
   save. Capture serial logs during the trial and preserve `/storage/init.log`
   and the confirmation journal where available. Revoke the test sequence after
   collecting evidence.
8. Qualify reset and power-loss boundaries, FAT state persistence, kernel hangs
   and hardware watchdog behavior separately. Keep a verified bootable image
   and disposable storage available before running fault injection.

A host-only report retains `HARDWARE_BOOT_UNVERIFIED`. Keep these artifacts,
checksums, source commit, and reports in Actions or draft releases. Firmware
publication requires the completed physical checklist.

## Current validation record

Candidate qualification on 2026-10-20 used the repository Docker build image
`sha256:11e3b58ed1d35d39fad71ab2adb5b0a385847c3fa2c1a1e0245d91af165eb78c`.
The complete AArch64 build finished all 696 package jobs. Full-image inspection
then caught the automatically selected FAT16 boot filesystem; after forcing
FAT32, the regenerated Generic image passed every check in the table above.
Earlier image-assembly failures exposed the missing selected host `unsquashfs`
and a validator bug handling Linux's NUL-terminated cpio symlink targets. Both
were repaired and covered before accepting an image.

| Executed qualification | Result |
| --- | --- |
| Package Python regressions | 92 tests passed |
| Updater safety, including real SquashFS/bsdiff lifecycle | 31 tests passed, no skips |
| FR9 EmulationStation shell integration | 24 checks passed |
| Embedded archive and target BusyBox | 68 cpio entries, 91 compiled applets; all 48 required applets validated |
| AArch64 QEMU runtime | `readlink -f`, ash dispatch, FIFO/tee log drain and packaged `evtest` passed |
| Initramfs ELF dependency audit | Eight command entries and twelve recursive AArch64 ELF objects resolved |
| Generic disk inspection | gzip, GPT, U-Boot, FAT32, RGB30 DTB, kernel/init, state/scripts, slots and EXT4 passed |
| Changed workflow syntax | `actionlint` passed |
| Physical RGB30 boot and rollback | **HARDWARE_BOOT_UNVERIFIED** |

The ELF dependency audit is static evidence. A separate QEMU loader `--list`
probe for `fsck` timed out; execution of every external helper is not claimed.
The logging adapters and hardware limits described above still apply.

Candidate artifacts were built while the repair was uncommitted. Publication
requires another build from the final commit, a matching rootfs `BUILD_ID`, and
fresh artifact validation. The release report binds that final source identity
to the published checksums; candidate hashes must not be substituted.

The delivered validation report and release notes are the authoritative record
for the final source commit, exact artifact hashes, executed tests and remaining
limitations. Intermediate failed builds and hardware-unverified status must be
retained there; this procedure document alone does not claim a boot result.
