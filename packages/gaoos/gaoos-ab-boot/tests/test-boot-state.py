#!/usr/bin/env python3
"""Exercise the actual boot.cmd transitions with an inert U-Boot command adapter."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[4]
BOOT = ROOT / 'packages/gaoos/gaoos-ab-boot/boot/boot.cmd'


def run_boot(directory, save_ok=True):
    adapter = '''
devtype=mmc devnum=1 scriptaddr=1000 kernel_addr_r=2000 fdt_addr_r=3000
fdtfile=rockchip/rk3566-powkiddy-rgb30.dtb
setenv() { local key=$1; shift; if [ -n "$*" ]; then printf -v "$key" '%s' "$*"; else unset "$key"; fi; }
load() { loaded=$4; filesize=100; [ -f "$ROOT/$loaded" ]; }
env() {
  case "$1" in
    import)
      shift 4
      while IFS='=' read -r key value; do
        if [ "$#" = 0 ]; then setenv "$key" "$value"; fi
        for allowed in "$@"; do
          if [ "$key" = "$allowed" ]; then setenv "$key" "$value"; fi
        done
      done < "$ROOT/$loaded" ;;
    export)
      shift 3
      : > "$ROOT/exported"
      for key; do
        if [[ -v $key ]]; then printf '%s=%s\\n' "$key" "${!key}" >> "$ROOT/exported"; fi
      done ;;
  esac
}
save() { [ "$SAVE_OK" = yes ] && cp "$ROOT/exported" "$ROOT/ab_state.env"; }
setexpr() {
  if [ "$2" = sub ]; then setenv "$1" "${5##*/}"
  else setenv "$1" "$(( $2 + $4 ))"; fi
}
fdt() { :; }
booti() { printf 'BOOTARGS=%s\\n' "$bootargs"; }
'''
    return subprocess.run(['bash', '-s'], input='ROOT=' + shlex.quote(str(directory)) +
                          '\nSAVE_OK=' + ('yes' if save_ok else 'no') + '\n' + adapter + BOOT.read_text(),
                          text=True, capture_output=True)


class BootState(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'gaoos_boot.env').write_text('extra_cmdline=quiet\ngaoos_fdt=\ngaoos_fdtdir=device_trees\ngaoos_overlays=\n')
        (self.root / 'KERNEL').write_bytes(b'kernel')
        (self.root / 'device_trees').mkdir()
        (self.root / 'device_trees/rk3566-powkiddy-rgb30.dtb').touch()

    def state(self, active='a', a='good', b='', retry_a=0, retry_b=0, extra=''):
        (self.root / 'ab_state.env').write_text(
            f'active_slot={active}\nstatus_a={a}\nstatus_b={b}\nretry_a={retry_a}\nretry_b={retry_b}\nmax_retries=3\n' + extra)

    def boot(self, slot=None, save_ok=True):
        result = run_boot(self.root, save_ok)
        self.assertNotIn('syntax error', result.stderr)
        if slot:
            self.assertIn('BOOTARGS=', result.stdout, result.stdout + result.stderr)
            self.assertIn('gaoos.slot=' + slot, result.stdout)
        else:
            self.assertNotIn('BOOTARGS=', result.stdout, result.stdout + result.stderr)
        return result

    def test_initial_a_is_pending_until_health_confirmation(self):
        self.state()
        self.boot('a')
        self.assertIn('status_a=pending', (self.root / 'ab_state.env').read_text())
        self.assertIn('retry_a=1', (self.root / 'ab_state.env').read_text())

    def test_three_failed_attempts_then_rollback_to_good_slot(self):
        self.state(active='b', a='good', b='pending')
        for count in (1, 2, 3):
            self.boot('b')
            self.assertIn('retry_b=' + str(count), (self.root / 'ab_state.env').read_text())
        self.boot('a')
        data = (self.root / 'ab_state.env').read_text()
        self.assertIn('active_slot=a', data)
        self.assertIn('status_b=bad', data)

    def test_both_bad_are_never_marked_good(self):
        self.state(a='bad', b='bad')
        self.boot()
        self.assertIn('status_a=bad', (self.root / 'ab_state.env').read_text())

    def test_exhausted_trial_cannot_boot_empty_fallback(self):
        self.state(a='pending', retry_a=3)
        self.boot()

    def test_missing_and_corrupt_metadata_recover_to_counted_a(self):
        for content in (None, 'active_slot=x\nretry_a=garbage\n', 'not=state\n'):
            path = self.root / 'ab_state.env'
            if path.exists():
                path.unlink()
            if content is not None:
                path.write_text(content)
            self.boot('a')
            self.assertIn('retry_a=1', path.read_text())

    def test_import_cannot_override_addresses_or_board_dtb(self):
        self.state(extra='fdtfile=wrong.dtb\nkernel_addr_r=BAD\n')
        self.boot('a')

    def test_state_write_failure_refuses_uncounted_boot(self):
        self.state()
        self.boot(save_ok=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
