#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Behavioral updater regressions. All device/network/system commands are isolated.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 - "${1:-${SCRIPT_DIR}/../src}" <<'PY'
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(sys.argv.pop(1)).resolve()


class UpdateSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="gaoos-update-test-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.storage = self.root / "storage"
        self.stage = self.storage / ".update"
        self.stage.mkdir(parents=True)
        self.flash = self.root / "flash"
        self.flash.mkdir()
        self.state = self.flash / "ab_state.env"
        self.original_state = b"active_slot=a\nstatus_a=good\nstatus_b=good\nretry_a=0\nretry_b=0\nmax_retries=3\n"
        self.state.write_bytes(self.original_state)
        (self.root / "cmdline").write_text("gaoos.slot=a gaoos.dev=mmcblk0\n")
        self.mount_slots("mmcblk0", "p")
        (self.root / "uptime").write_text("100.0 20.0\n")
        (self.root / "device").write_text("RK3566\n")
        (self.root / "version").write_text("1\n")
        (self.root / "profile").touch()
        self.slot_a = self.root / "slot-a"
        self.slot_b = self.root / "slot-b"
        self.slot_a.write_bytes(self.image(4096))
        self.slot_b.write_bytes(b"old slot" + bytes(12 * 1024 * 1024 - 8))
        self.devices = self.root / "dev"
        self.devices.mkdir()
        (self.devices / "mmcblk0p2").symlink_to(self.slot_a)
        (self.devices / "mmcblk0p3").symlink_to(self.slot_b)
        self.image_file = self.root / "image.squashfs"
        self.image_file.write_bytes(self.image(4096, b"new"))
        substitutions = {
            '"/dev/${dev}${sep}2"': '"${TEST_ROOT}/dev/${dev}${sep}2"',
            '"/dev/${dev}${sep}3"': '"${TEST_ROOT}/dev/${dev}${sep}3"',
            '[ -b "${dev}" ]': '[ -f "${dev}" ]',
            "/proc/cmdline": str(self.root / "cmdline"),
            "/proc/device-tree/compatible": str(self.root / "compatible"),
            "/proc/sys/kernel/random/uuid": str(self.root / "uuid"),
            "/proc/sys/kernel/random/boot_id": str(self.root / "boot-id"),
            "/proc/mounts": str(self.root / "mounts"),
            "/proc/self/mountinfo": str(self.root / "mountinfo"),
            "/sys/class/block": str(self.root / "sys"),
            "/proc/uptime": str(self.root / "uptime"),
            "/etc/gaoos-device": str(self.root / "device"),
            "/etc/gaoos-version": str(self.root / "version"),
            "/etc/gaoos-ota-sequence": str(self.root / "sequence"),
            "/etc/profile": str(self.root / "profile"),
            "/usr/lib/gaoos/gaoos-ota-client.sh": str(self.bin / "gaoos-ota-client.sh"),
            "/usr/bin/gaoos-boot-confirm": str(self.bin / "gaoos-boot-confirm"),
            "/usr/bin/gaoos-ota-report": str(self.bin / "gaoos-ota-report"),
            "/usr/bin/gaoos-ota-helper": str(self.bin / "gaoos-ota-helper"),
            "/usr/share/sway/config.desktop": str(self.root / "config.desktop"),
            "/run/rocknix/start.games": str(self.root / "start.games"),
            "/run/gaoos-ab.lock": str(self.root / "ab.lock"),
            "/run/gaoos-ota.lock": str(self.root / "ota.lock"),
            "/var/log/gaoos-boot-confirm.log": str(self.root / "confirm.log"),
            "/tmp/gaoos-update-progress": str(self.root / "progress"),
            "/storage": str(self.storage),
            "/flash": str(self.flash),
        }
        for name in ("gaoos-update", "gaoos-boot-confirm", "gaoos-reboot-update"):
            text = (SOURCE / name).read_text()
            for old, new in substitutions.items():
                text = text.replace(old, new)
            self.executable(name, text)
        self.substitutions = substitutions
        self.executable("flock", '''#!/usr/bin/env python3
import fcntl, sys
mode = fcntl.LOCK_EX
if "-n" in sys.argv:
    mode |= fcntl.LOCK_NB
try:
    fcntl.flock(int(sys.argv[-1]), mode)
except BlockingIOError:
    sys.exit(1)
''')
        self.executable("stat", '''#!/usr/bin/env python3
import os, pathlib, sys
if sys.argv[1:3] == ["-Lc", "%t:%T"]:
    name = pathlib.Path(sys.argv[-1]).resolve().name
    print({"slot-a": "b3:2", "slot-b": "b3:3"}.get(name, "b3:9"))
else:
    os.execv(os.environ["REAL_STAT"], sys.argv)
''')
        self.executable("readlink", '''#!/bin/bash
if [ "$1" = -f ] && [[ "$2" == /dev/* ]]; then
  printf '%s\n' "$2"
else
  exec "$REAL_READLINK" "$@"
fi
''')
        self.executable("mount", "#!/bin/bash\nexit 0\n")
        self.executable("mountpoint", "#!/bin/bash\nexit 0\n")
        self.executable("reboot", '#!/bin/bash\ntouch "$TEST_ROOT/rebooted"\n')
        self.executable("sleep", "#!/bin/bash\nexit 0\n")
        self.executable("systemctl", '''#!/bin/bash
case "$*" in
  "is-active --quiet multi-user.target"|"is-active --quiet graphical.target"|"is-active --quiet rocknix-autostart.service") [ "${SYSTEM_HEALTH:-good}" = good ] ;;
  "is-active --quiet sway.service") [ "${SWAY_HEALTH:-${UI_HEALTH:-good}}" = good ] ;;
  "is-active --quiet essway.service") [ "${ESSWAY_HEALTH:-${UI_HEALTH:-good}}" = good ] ;;
  "is-enabled essway.service") printf '%s\\n' "${ESSWAY_STATE:-disabled}"; [ "${ESSWAY_STATE:-disabled}" = enabled ] ;;
  *) exit 1 ;;
esac
''')
        self.executable("sync", "#!/bin/bash\n[ \"${SYNC_FAIL:-0}\" = 0 ]\n")
        self.executable("cat", '''#!/bin/bash
if [ "$#" = 0 ] && [ "${CAT_FAIL:-0}" = 1 ]; then
  exit 1
fi
exec "$REAL_CAT" "$@"
''')
        self.executable("mv", '''#!/bin/bash
for arg in "$@"; do
  [[ "$arg" == -* || "$arg" == "$TEST_ROOT/"* ]] || exit 99
done
[ "${MV_FAIL:-0}" = 0 ] || exit 1
exec "$REAL_MV" "$@"
''')
        self.executable("blockdev", '''#!/usr/bin/env python3
import os, pathlib, sys
path = pathlib.Path(sys.argv[-1])
assert str(path).startswith(os.environ['TEST_ROOT'] + '/')
print(path.stat().st_size)
''')
        self.executable("unsquashfs", '''#!/usr/bin/env python3
import pathlib, sys
data = pathlib.Path(sys.argv[-1]).read_bytes()
sys.exit(0 if len(data) >= 96 and data[:4] == b'hsqs' else 1)
''')
        self.executable("dd", '''#!/usr/bin/env python3
import os, pathlib, sys
args = sys.argv[1:]
if os.environ.get('DD_BUSYBOX_CONTRACT') == '1':
    # BusyBox 1.38.0 coreutils/dd.c accepts only these status values.
    for arg in args:
        if arg.startswith('status=') and arg[7:] not in ('none', 'noxfer'):
            sys.exit(1)
for arg in args:
    if arg.startswith(('if=', 'of=')):
        assert arg[3:].startswith(os.environ['TEST_ROOT'] + '/')
if (os.environ.get('DD_FAIL') == '1' or os.environ.get('DD_INCOMPLETE') == '1') and any(a.startswith('of=') for a in args):
    destination = pathlib.Path(next(a[3:] for a in args if a.startswith('of=')))
    with destination.open('r+b') as file:
        file.write(b'partial write')
    sys.exit(1 if os.environ.get('DD_FAIL') == '1' else 0)
args = [a.replace('conv=fsync', 'conv=fsync,notrunc') for a in args]
os.execv(os.environ['REAL_DD'], ['dd'] + args)
''')
        self.executable("bspatch", '''#!/usr/bin/env python3
import os, pathlib, sys
source, target, patch = map(pathlib.Path, sys.argv[1:])
for path in (source, target, patch):
    assert str(path).startswith(os.environ['TEST_ROOT'] + '/')
# A patcher boundary fixture: old image must be extracted to a regular staging file.
assert source.name != 'slot-a', 'bspatch was given the block partition directly'
assert source.read_bytes() == pathlib.Path(os.environ['TEST_ROOT'], 'expected-base').read_bytes()
target.write_bytes(pathlib.Path(os.environ['TEST_ROOT'], 'reconstructed').read_bytes())
''')
        self.env = dict(os.environ, PATH=str(self.bin) + ":" + os.environ["PATH"],
                        TEST_ROOT=str(self.root),
                        REAL_DD=shutil.which("gdd") or shutil.which("dd"),
                        REAL_STAT=shutil.which("gstat") or shutil.which("stat"),
                        REAL_READLINK=shutil.which("readlink"),
                        REAL_MV=shutil.which("mv"), REAL_CAT=shutil.which("cat"))

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def image(size, marker=b"old"):
        data = bytearray(size)
        data[:4] = b"hsqs"
        struct.pack_into("<HH", data, 28, 4, 0)
        struct.pack_into("<Q", data, 40, size)
        data[96:96 + len(marker)] = marker
        return bytes(data)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def executable(self, name, text):
        path = self.bin / name
        path.write_text(text)
        path.chmod(0o755)

    def run_command(self, *args, **environment):
        return subprocess.run(args, env=dict(self.env, **environment), text=True,
                              capture_output=True, timeout=20)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def mount_slots(self, disk, separator):
        base = "/dev/" + disk + separator
        (self.root / "mounts").write_text(
            f"{base}1 {self.flash} vfat rw,relatime 0 0\n"
            f"{base}2 / squashfs ro,relatime 0 0\n"
            f"{base}4 {self.storage} ext4 rw,relatime 0 0\n"
        )
        (self.root / "mountinfo").write_text(
            f"1 0 179:2 / / ro,relatime - squashfs {base}2 ro\n"
            f"2 1 179:1 / {self.flash} rw,relatime - vfat {base}1 rw\n"
            f"3 1 179:4 / {self.storage} rw,relatime - ext4 {base}4 rw\n"
        )
        for partition in (2, 3):
            sysfs = self.root / "sys" / f"{disk}{separator}{partition}"
            sysfs.mkdir(parents=True, exist_ok=True)
            (sysfs / "dev").write_text(f"179:{partition}\n")

    def test_wrong_sidecar_never_modifies_target(self):
        Path(str(self.image_file) + ".sha256").write_text("0" * 64 + "  " + str(self.image_file) + "\n")
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assertNotEqual(result.returncode, 0, "wrong source checksum was accepted")
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_non_aligned_image_verifies_and_commits_pending(self):
        self.image_file.write_bytes(self.image(9 * 1024 * 1024))
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assert_success(result)
        self.assertEqual(self.slot_b.read_bytes()[:self.image_file.stat().st_size], self.image_file.read_bytes())
        self.assertIn("active_slot=b\n", self.state.read_text())

    def test_flash_disk_overrides_mismatched_cmdline_mmc_number(self):
        self.mount_slots("mmcblk1", "p")
        (self.devices / "mmcblk1p2").symlink_to(self.slot_a)
        (self.devices / "mmcblk1p3").symlink_to(self.slot_b)
        wrong_partition = self.devices / "mmcblk0p3"
        wrong_partition.unlink()
        wrong_contents = b"other disk" + bytes(16384)
        wrong_partition.write_bytes(wrong_contents)
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assert_success(result)
        self.assertEqual(wrong_partition.read_bytes(), wrong_contents)
        self.assertEqual(self.slot_b.read_bytes()[:4096], self.image_file.read_bytes())
        self.assertIn("active_slot=b\n", self.state.read_text())

    def test_supported_flash_disk_partition_names(self):
        for disk, separator in (("mmcblk1", "p"), ("loop1", "p"), ("nvme0n1", "p"), ("sda", "")):
            with self.subTest(disk=disk):
                self.state.write_bytes(self.original_state)
                self.mount_slots(disk, separator)
                for number, target in ((2, self.slot_a), (3, self.slot_b)):
                    (self.devices / (disk + separator + str(number))).symlink_to(target)
                result = self.run_command("gaoos-update", "apply", str(self.image_file))
                self.assert_success(result)
                self.assertIn(str(self.devices / (disk + separator + "3")), result.stderr)

    def test_missing_flash_mount_fails_without_target_write(self):
        (self.root / "mounts").write_text("")
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_failed_slot_write_keeps_active_and_retires_inactive_slot(self):
        running_before = self.slot_a.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file), DD_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_a.read_bytes(), running_before)
        self.assertEqual(self.state.read_bytes(), self.original_state.replace(b"status_b=good", b"status_b=bad"))
        self.assertNotEqual(self.run_command("gaoos-update", "rollback").returncode, 0)

    def test_partial_write_with_success_status_cannot_be_selected(self):
        result = self.run_command("gaoos-update", "apply", str(self.image_file), DD_INCOMPLETE="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Write verification failed", result.stderr)
        self.assertEqual(self.state.read_bytes(), self.original_state.replace(b"status_b=good", b"status_b=bad"))

    def test_quarantine_failure_leaves_partition_and_state_untouched(self):
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file), SYNC_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_mount_device_mismatch_does_not_touch_inactive_slot(self):
        before = self.slot_b.read_bytes()
        mountinfo = self.root / "mountinfo"
        original = mountinfo.read_text()
        for root in ("179:3 / / ro,relatime", "179:7 / / ro,relatime", "179:2 / / rw,relatime"):
            with self.subTest(root=root):
                mountinfo.write_text(original.replace("179:2 / / ro,relatime", root))
                result = self.run_command("gaoos-update", "apply", str(self.image_file))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.slot_b.read_bytes(), before)
                self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_inactive_device_mounted_through_alias_is_rejected(self):
        mountinfo = self.root / "mountinfo"
        with mountinfo.open("a") as stream:
            stream.write("4 1 179:3 / /other ro - squashfs /dev/disk/by-label/other ro\n")
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_alias_to_running_slot_is_rejected(self):
        target = self.devices / "mmcblk0p3"
        target.unlink()
        target.symlink_to(self.slot_a)
        before = self.slot_a.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_a.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_pending_boot_or_existing_stage_prevents_replacing_rollback_copy(self):
        before = self.slot_b.read_bytes()
        for staged in (self.original_state.replace(b"status_a=good", b"status_a=pending"),
                       self.original_state.replace(b"active_slot=a", b"active_slot=b")
                       .replace(b"status_b=good", b"status_b=pending")):
            self.state.write_bytes(staged)
            result = self.run_command("gaoos-update", "apply", str(self.image_file))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.slot_b.read_bytes(), before)
            self.assertEqual(self.state.read_bytes(), staged)

    def test_apply_and_rollback_share_exclusive_state_lock(self):
        before = self.slot_b.read_bytes()
        with (self.root / "ab.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            for command in (("apply", str(self.image_file)), ("rollback",)):
                self.assertNotEqual(self.run_command("gaoos-update", *command).returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_signed_apply_rechecks_integrity_before_quarantine(self):
        self.executable("gaoos-ota-client.sh", """cmd_ota_apply() { cmd_apply "$1" "$EXPECTED_HASH" "$EXPECTED_SIZE"; }\n""")
        healthy = dict(EXPECTED_HASH=self.digest(self.image_file), EXPECTED_SIZE=str(self.image_file.stat().st_size))
        before = self.slot_b.read_bytes()
        for invalid in (dict(healthy, EXPECTED_HASH="0" * 64), dict(healthy, EXPECTED_SIZE="1")):
            result = self.run_command("gaoos-update", "apply", str(self.image_file), **invalid)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.slot_b.read_bytes(), before)
            self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assert_success(self.run_command("gaoos-update", "apply", str(self.image_file), **healthy))
        self.assertEqual(self.slot_b.read_bytes()[:4096], self.image_file.read_bytes())
        self.assertIn(b"status_b=pending\n", self.state.read_bytes())

    def prepare_live_offer(self):
        mksquashfs = shutil.which("mksquashfs")
        unsquashfs = shutil.which("unsquashfs")
        if not mksquashfs or not unsquashfs:
            self.skipTest("SquashFS tools are required")
        (self.bin / "unsquashfs").unlink()
        (self.bin / "unsquashfs").symlink_to(unsquashfs)
        self.executable("uname", "#!/bin/bash\nprintf '%s\\n' aarch64\n")
        self.executable("gaoos-ota-client.sh", self.remap("gaoos-ota-client.sh"))
        self.executable("gaoos-ota-report", self.remap("gaoos-ota-report"))
        (self.storage / ".config").mkdir()
        (self.storage / ".config" / "gaoos-ab.conf").write_text('UPDATE_API_URL="https://ota.test"\n')
        (self.root / "compatible").write_bytes(b"powkiddy,rgb30\0rockchip,rk3566\0")
        (self.root / "uuid").write_text("00000000-0000-4000-a000-000000000011\n")
        (self.root / "boot-id").write_text("00000000-0000-4000-a000-000000000022\n")
        (self.root / "cmdline").write_text(
            "gaoos.slot=a gaoos.fdt=device_trees/rk3566-powkiddy-rgb30.dtb\n")
        (self.flash / "gaoos_boot.env").write_text(
            "extra_cmdline=\ngaoos_fdt=\ngaoos_fdtdir=device_trees\ngaoos_overlays=\n")
        (self.flash / "KERNEL").write_bytes(b"shared kernel")
        (self.flash / "device_trees").mkdir()
        (self.flash / "device_trees" / "rk3566-powkiddy-rgb30.dtb").write_bytes(b"shared dtb")
        root = self.root / "fixture-rootfs"
        (root / "etc").mkdir(parents=True)
        (root / "etc" / "gaoos-version").write_text("2\n")
        (root / "etc" / "gaoos-ota-sequence").write_text("2\n")
        self.image_file.unlink()
        subprocess.run([mksquashfs, str(root), str(self.image_file), "-noappend", "-processors", "1"],
                       capture_output=True, check=True)
        release = "00000000-0000-4000-a000-000000000033"
        self.executable("gaoos-ota-helper", f'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
root = pathlib.Path(os.environ["TEST_ROOT"])
if sys.argv[1] == "event":
    with (root / "received.jsonl").open("a") as stream:
        stream.write(pathlib.Path(sys.argv[sys.argv.index("--event-file") + 1]).read_text())
    sys.exit(0)
if (root / "no-offer").exists():
    sys.exit(3)
if sys.argv[1] == "download":
    assert sys.argv[sys.argv.index("--release-id") + 1] == {release!r}
    stage = pathlib.Path(sys.argv[sys.argv.index("--dir") + 1])
    shutil.copyfile(root / "image.squashfs", stage / "release-{release}.squashfs")
print("2\\t{self.digest(self.image_file)}\\t{self.image_file.stat().st_size}\\t2\\t{release}")
''')
        return release

    def remap(self, name):
        text = (SOURCE / name).read_text()
        for old, new in self.substitutions.items():
            text = text.replace(old, new)
        return text

    def test_revoked_download_cannot_apply_or_change_boot_state(self):
        release = self.prepare_live_offer()
        result = self.run_command("gaoos-update", "download")
        self.assert_success(result)
        self.assertIn(release, result.stdout)
        downloaded = result.stdout.strip()
        (self.root / "no-offer").touch()
        before = self.slot_b.read_bytes()
        self.assertNotEqual(self.run_command("gaoos-update", "apply", downloaded).returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_canary_stages_without_reboot_and_reports_reconciled_rollback_once(self):
        release = self.prepare_live_offer()
        self.assert_success(self.run_command("gaoos-update", "canary"))
        self.assertFalse((self.root / "rebooted").exists())
        self.assertIn(b"status_b=pending\n", self.state.read_bytes())
        self.assertEqual(self.slot_b.read_bytes()[:self.image_file.stat().st_size], self.image_file.read_bytes())

        self.state.write_bytes(self.original_state.replace(b"status_b=good", b"status_b=bad")
                               .replace(b"retry_b=0", b"retry_b=3"))
        for _ in range(2):
            self.assert_success(self.run_command("gaoos-ota-report", "boot"))
            self.assert_success(self.run_command("gaoos-ota-report", "flush"))
        received = [json.loads(line) for line in (self.root / "received.jsonl").read_text().splitlines()]
        self.assertCountEqual([event["type"] for event in received], ["downloaded", "staged", "rolled_back"])
        self.assertTrue(all(event["release_id"] == release for event in received))
        self.assertEqual(len({event["attempt_id"] for event in received}), 1)

    def test_rejected_reports_do_not_block_later_events(self):
        self.executable("gaoos-ota-client.sh", self.remap("gaoos-ota-client.sh"))
        self.executable("gaoos-ota-report", self.remap("gaoos-ota-report"))
        (self.storage / ".config").mkdir()
        (self.storage / ".config" / "gaoos-ab.conf").write_text('UPDATE_API_URL="https://ota.test"\n')
        self.executable("gaoos-ota-helper", '''#!/bin/bash
case "${!#}" in
  */old-*) exit 4 ;;
  */transient.json) exit 1 ;;
  *) exit 0 ;;
esac
''')
        ota_dir = self.stage / ".ota"
        queued = ota_dir / "events"
        queued.mkdir(parents=True)
        for i in range(5):
            (queued / f"old-{i}.json").write_text(f"old event {i}")
        (queued / "ready.json").write_text("acknowledged event")
        (queued / "transient.json").write_text("retryable event")

        self.assert_success(self.run_command("gaoos-ota-report", "flush"))
        self.assert_success(self.run_command("gaoos-ota-report", "flush"))

        rejected = ota_dir / "rejected-events"
        self.assertEqual(sorted(p.name for p in queued.iterdir()), ["transient.json"])
        self.assertEqual((queued / "transient.json").read_text(), "retryable event")
        self.assertEqual(sorted(p.read_text() for p in rejected.iterdir()),
                         [f"old event {i}" for i in range(5)])

    def test_interrupted_write_intent_can_retry_without_reboot_on_empty_slot(self):
        release = self.prepare_live_offer()
        self.state.write_bytes(self.original_state.replace(b"status_b=good\n", b""))
        ota_dir = self.stage / ".ota"
        ota_dir.mkdir()
        abandoned = "00000000-0000-4000-a000-000000000044"
        boot_id = (self.root / "boot-id").read_text().strip()
        (ota_dir / "install").write_text(f"{release}\t{abandoned}\ta\tb\t2\t2\t{boot_id}\tintent\n")
        (ota_dir / f"attempt-{release}").write_text(abandoned + "\n")
        self.assert_success(self.run_command("gaoos-update", "canary"))
        self.assertIn(b"status_b=pending\n", self.state.read_bytes())
        self.assertFalse((self.root / "rebooted").exists())
        event = json.loads((ota_dir / "events" / f"{abandoned}-failed.json").read_text())
        self.assertEqual(event["detail"], "write")
        self.assertFalse((ota_dir / "events" / f"{abandoned}-staged.json").exists())

    def test_pending_manual_install_cannot_become_an_ota_attempt(self):
        self.prepare_live_offer()
        download = self.run_command("gaoos-update", "download")
        self.assert_success(download)
        manual_state = self.original_state.replace(b"active_slot=a", b"active_slot=b")
        manual_state = manual_state.replace(b"status_b=good", b"status_b=pending")
        self.state.write_bytes(manual_state)
        result = self.run_command("gaoos-update", "apply", download.stdout.strip())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), manual_state)
        self.assertFalse((self.stage / ".ota" / "install").exists())

    def test_reboot_menu_checks_state_as_data_and_requires_a_pending_next_boot(self):
        rebooted = self.root / "rebooted"
        for state in (self.original_state, self.original_state + b"echo=unsafe\n",
                      self.original_state.replace(b"active_slot=a", b"active_slot=b")):
            self.state.write_bytes(state)
            self.assertNotEqual(self.run_command("gaoos-reboot-update").returncode, 0)
            self.assertFalse(rebooted.exists())
        self.state.write_bytes(self.original_state)
        self.assert_success(self.run_command("gaoos-update", "apply", str(self.image_file)))
        self.assertFalse(rebooted.exists())
        self.assert_success(self.run_command("gaoos-reboot-update"))
        self.assertTrue(rebooted.exists())

    def test_busybox_dd_contract_writes_verified_image(self):
        source_before = self.slot_a.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(self.image_file), DD_BUSYBOX_CONTRACT="1")
        self.assert_success(result)
        self.assertEqual(self.slot_b.read_bytes()[:self.image_file.stat().st_size], self.image_file.read_bytes())
        self.assertEqual(self.slot_a.read_bytes(), source_before)
        self.assertIn("active_slot=b\n", self.state.read_text())
        self.assertIn("status_b=pending\n", self.state.read_text())

    def test_state_flush_failure_preserves_old_state(self):
        self.original_state = self.original_state.replace(b"status_a=good", b"status_a=pending")
        self.state.write_bytes(self.original_state)
        result = self.run_command("gaoos-update", "confirm", SYNC_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertEqual(list(self.flash.glob("ab_state.env.*")), [])

    def test_state_rename_failure_preserves_old_state(self):
        result = self.run_command("gaoos-update", "rollback", MV_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertEqual(list(self.flash.glob("ab_state.env.*")), [])

    def test_boot_confirm_flush_failure_preserves_pending(self):
        self.original_state = self.original_state.replace(b"status_a=good", b"status_a=pending").replace(b"retry_a=0", b"retry_a=2")
        self.state.write_bytes(self.original_state)
        result = self.run_command("gaoos-boot-confirm", SYNC_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertEqual(list(self.flash.glob("ab_state.env.*")), [])

    def test_temporary_state_write_failure_preserves_pending(self):
        self.original_state = self.original_state.replace(b"status_a=good", b"status_a=pending")
        for command in (("gaoos-update", "confirm"), ("gaoos-boot-confirm",)):
            with self.subTest(command=command):
                self.state.write_bytes(self.original_state)
                result = self.run_command(*command, CAT_FAIL="1")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_bytes(), self.original_state)
                self.assertEqual(list(self.flash.glob("ab_state.env.*")), [])

    def test_boot_confirm_success_resets_retry(self):
        self.state.write_bytes(self.original_state.replace(b"status_a=good", b"status_a=pending").replace(b"retry_a=0", b"retry_a=2"))
        self.assert_success(self.run_command("gaoos-boot-confirm"))
        self.assertEqual(self.state.read_bytes(), self.original_state)

    def test_confirmation_releases_lock_before_best_effort_reporting(self):
        self.executable("gaoos-ota-report", '''#!/usr/bin/env python3
import fcntl, os, pathlib, sys
assert sys.argv[1:] == ["boot"]
root = pathlib.Path(os.environ["TEST_ROOT"])
with (root / "ab.lock").open("w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
(root / "reported").touch()
sys.exit(1)
''')
        self.state.write_bytes(self.original_state.replace(b"status_a=good", b"status_a=pending"))
        self.assert_success(self.run_command("gaoos-boot-confirm"))
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertTrue((self.root / "reported").exists())

    def test_factory_uboot_export_omits_empty_status_b(self):
        self.state.write_bytes(self.original_state.replace(b"status_a=good", b"status_a=pending").replace(b"status_b=good\n", b""))
        self.assert_success(self.run_command("gaoos-boot-confirm"))
        self.assertIn("status_a=good", self.state.read_text())
        self.assertIn("status_b=\n", self.state.read_text())

    def test_missing_or_malicious_state_cannot_confirm(self):
        for data in (b"active_slot=a\n", self.original_state + b"echo=MALICIOUS\n", self.original_state.replace(b"retry_a=0", b"retry_a=invalid")):
            self.state.write_bytes(data)
            result = self.run_command("gaoos-boot-confirm")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.state.read_bytes(), data)

    def test_boot_confirm_rejects_wrong_or_corrupt_root(self):
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        original_mounts = (self.root / "mounts").read_text()
        for change in ("/dev/mmcblk0p3 / squashfs", "/dev/mmcblk1p2 / squashfs", "/dev/mmcblk0p2 / ext4"):
            with self.subTest(root=change):
                self.state.write_bytes(pending)
                (self.root / "mounts").write_text(original_mounts.replace("/dev/mmcblk0p2 / squashfs", change))
                self.assertNotEqual(self.run_command("gaoos-boot-confirm").returncode, 0)
                self.assertEqual(self.state.read_bytes(), pending)

    def test_boot_confirm_rejects_readonly_storage_or_unhealthy_userspace(self):
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        self.state.write_bytes(pending)
        self.assertNotEqual(self.run_command("gaoos-boot-confirm", SYSTEM_HEALTH="failed").returncode, 0)
        self.assertEqual(self.state.read_bytes(), pending)

    def test_manual_confirm_uses_the_same_health_checks(self):
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        self.state.write_bytes(pending)
        self.assertNotEqual(self.run_command("gaoos-update", "confirm", SYSTEM_HEALTH="failed").returncode, 0)
        self.assertEqual(self.state.read_bytes(), pending)

    def test_configured_sway_frontend_is_healthy_and_failure_stays_pending(self):
        profile = self.storage / ".config/profile.d/090-ui_service"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('UI_SERVICE="sway.service essway.service"\n')
        (self.root / "start.games").touch()
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        self.state.write_bytes(pending)
        self.assert_success(self.run_command("gaoos-boot-confirm"))
        self.state.write_bytes(pending)
        self.assertNotEqual(self.run_command("gaoos-boot-confirm", UI_HEALTH="failed").returncode, 0)
        self.assertEqual(self.state.read_bytes(), pending)
        mounts = self.root / "mounts"
        mounts.write_text(mounts.read_text().replace("ext4 rw,relatime", "ext4 ro,relatime"))
        self.assertNotEqual(self.run_command("gaoos-boot-confirm").returncode, 0)
        self.assertEqual(self.state.read_bytes(), pending)

    def test_boot_confirm_rejects_missing_or_invalid_slot_argument(self):
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        for commandline in ("quiet", "gaoos.slot=bad", "gaoos.slot=a gaoos.slot=b"):
            self.state.write_bytes(pending)
            (self.root / "cmdline").write_text(commandline + "\n")
            self.assertNotEqual(self.run_command("gaoos-boot-confirm").returncode, 0)
            self.assertEqual(self.state.read_bytes(), pending)

    def prepare_frontend_health(self, desktop):
        profile = self.storage / ".config/profile.d/090-ui_service"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text('UI_SERVICE="sway.service essway.service"\n')
        (self.root / "start.games").touch()
        template = "desktop template\n"
        (self.root / "config.desktop").write_text(template)
        config = self.storage / ".config/sway/config"
        config.parent.mkdir(parents=True, exist_ok=True)
        # Sway startup copies the selected template, then appends output setup.
        config.write_text((template if desktop else "kiosk template\n") + "output DSI-1 transform 0\n")
        pending = self.original_state.replace(b"status_a=good", b"status_a=pending")
        self.state.write_bytes(pending)
        return pending

    def test_intended_sway_desktop_can_confirm_with_masked_game_frontend(self):
        self.prepare_frontend_health(desktop=True)
        result = self.run_command("gaoos-boot-confirm", ESSWAY_STATE="masked", ESSWAY_HEALTH="inactive")
        self.assert_success(result)
        self.assertIn("status_a=good\n", self.state.read_text())

    def test_kiosk_cannot_confirm_a_failed_or_unexpectedly_masked_game_frontend(self):
        pending = self.prepare_frontend_health(desktop=False)
        for state in ("disabled", "masked"):
            with self.subTest(essway_state=state):
                self.state.write_bytes(pending)
                result = self.run_command("gaoos-boot-confirm", ESSWAY_STATE=state, ESSWAY_HEALTH="failed")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_bytes(), pending)

    def test_desktop_exemption_requires_active_compositor_and_explicit_mask(self):
        pending = self.prepare_frontend_health(desktop=True)
        for state, sway in (("masked", "failed"), ("disabled", "good")):
            with self.subTest(essway_state=state, sway=sway):
                self.state.write_bytes(pending)
                result = self.run_command("gaoos-boot-confirm", ESSWAY_STATE=state,
                                          ESSWAY_HEALTH="inactive", SWAY_HEALTH=sway)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.state.read_bytes(), pending)

    def prepare_delta(self):
        base = self.slot_a.read_bytes()
        (self.root / "expected-base").write_bytes(base)
        self.slot_a.write_bytes(base + bytes(4096))
        reconstructed = self.image(8192, b"reconstructed")
        (self.root / "reconstructed").write_bytes(reconstructed)
        patch = self.stage / "image.patch"
        patch.write_bytes(b"BSDIFF40fixture")
        Path(str(patch) + ".sha256").write_text(self.digest(patch) + "  " + str(patch) + "\n")
        Path(str(patch) + ".target.sha256").write_text(hashlib.sha256(reconstructed).hexdigest() + "\n")
        return patch

    def test_delta_verifies_reconstruction_and_commits_pending(self):
        patch = self.prepare_delta()
        result = self.run_command("gaoos-update", "apply", str(patch))
        self.assert_success(result)
        self.assertEqual(self.slot_b.read_bytes()[:8192], (self.root / "reconstructed").read_bytes())
        self.assertIn("status_b=pending\n", self.state.read_text())
        self.assertEqual(list(self.stage.glob(".delta-*")), [])

    def test_delta_wrong_target_checksum_preserves_partition(self):
        patch = self.prepare_delta()
        Path(str(patch) + ".target.sha256").write_text("0" * 64 + "\n")
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(patch))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertEqual(list(self.stage.glob(".delta-*")), [])

    def test_delta_missing_target_checksum_preserves_partition(self):
        patch = self.prepare_delta()
        Path(str(patch) + ".target.sha256").unlink()
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(patch))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)

    def test_delta_invalid_source_bounds_preserves_partition(self):
        patch = self.prepare_delta()
        data = bytearray(self.slot_a.read_bytes())
        struct.pack_into("<Q", data, 40, len(data) + 4096)
        self.slot_a.write_bytes(data)
        before = self.slot_b.read_bytes()
        result = self.run_command("gaoos-update", "apply", str(patch))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.slot_b.read_bytes(), before)
        self.assertEqual(list(self.stage.glob(".delta-*")), [])

    @unittest.skipUnless(shutil.which("mksquashfs") and shutil.which("unsquashfs"),
                         "real SquashFS tools unavailable")
    def test_real_squashfs_staging_preserves_running_image(self):
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "version").write_text("updated system\n")
        artifact = self.root / "real.squashfs"
        subprocess.run([shutil.which("mksquashfs"), str(tree), str(artifact), "-noappend", "-processors", "1"],
                       check=True, capture_output=True, timeout=20)
        self.executable("unsquashfs", '#!/bin/bash\nexec "' + shutil.which("unsquashfs") + '" "$@"\n')
        running_before = self.slot_a.read_bytes()
        self.assert_success(self.run_command("gaoos-update", "apply", str(artifact)))
        self.assertEqual(self.slot_a.read_bytes(), running_before)
        self.assertEqual(self.slot_b.read_bytes()[:artifact.stat().st_size], artifact.read_bytes())
        self.assertIn(b"status_b=pending\n", self.state.read_bytes())

    @unittest.skipUnless(all(shutil.which(name) for name in ("mksquashfs", "unsquashfs", "bsdiff", "bspatch")),
                         "real SquashFS/bsdiff tools unavailable")
    def test_real_squashfs_delta_lifecycle(self):
        old_tree = self.root / "old-tree"
        new_tree = self.root / "new-tree"
        old_tree.mkdir()
        new_tree.mkdir()
        (old_tree / "version").write_text("old system\n")
        (new_tree / "version").write_text("new system\n")
        old_image = self.root / "old.squashfs"
        new_image = self.root / "new.squashfs"
        for tree, image in ((old_tree, old_image), (new_tree, new_image)):
            subprocess.run([shutil.which("mksquashfs"), str(tree), str(image), "-noappend", "-processors", "1"],
                           check=True, capture_output=True, timeout=20)
        patch = self.stage / "real.patch"
        subprocess.run([shutil.which("bsdiff"), str(old_image), str(new_image), str(patch)], check=True, timeout=20)
        self.slot_a.write_bytes(old_image.read_bytes() + bytes(4096))
        Path(str(patch) + ".sha256").write_text(self.digest(patch) + "\n")
        Path(str(patch) + ".target.sha256").write_text(self.digest(new_image) + "\n")
        # Replace only the patcher/validator mocks; target writes remain isolated.
        for name in ("bspatch", "unsquashfs"):
            self.executable(name, '#!/bin/bash\nexec "' + shutil.which(name) + '" "$@"\n')
        self.assert_success(self.run_command("gaoos-update", "apply", str(patch)))
        self.assertEqual(self.slot_b.read_bytes()[:new_image.stat().st_size], new_image.read_bytes())
        self.assertIn("status_b=pending\n", self.state.read_text())
        self.assertEqual(list(self.stage.glob(".delta-*")), [])


unittest.main(verbosity=2)
PY
