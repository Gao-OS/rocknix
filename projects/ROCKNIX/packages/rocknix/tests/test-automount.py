#!/usr/bin/env python3
"""Run the real ROM-source selection against temporary card layouts."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[5]
AUTOMOUNT = ROOT / 'projects/ROCKNIX/packages/rocknix/sources/scripts/automount'


class AutomountLayout(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='gaoos-rom-layout-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.storage = self.root / 'storage'
        self.external = self.storage / 'games-external'
        self.internal = self.storage / 'games-internal'
        self.external.mkdir(parents=True)
        self.internal.mkdir()
        (self.storage / 'roms').mkdir()
        self.config = self.root / 'usr/config/emulationstation/es_systems.cfg'
        self.config.parent.mkdir(parents=True)
        self.config.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<systemList>
  <system>
    <name>gba</name>
    <path>/storage/roms/gba</path>
  </system>
  <system>
    <name>snes</name>
    <path>/storage/roms/snes</path>
  </system>
</systemList>
'''.replace('/storage', str(self.storage)))

    def game(self, path, contents='game fixture'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def run_mount(self, device='external', os_name='GaoOS', merged=0):
        settings = self.root / 'system.cfg'
        original = (f'system.automount=1\nsystem.merged.storage={merged}\n'
                    'system.merged.device=external\ncustom.setting=preserve\n')
        settings.write_text(original)
        source = AUTOMOUNT.read_text().split('\nstart_overlay() {', 1)[0]
        source = source.replace('. /etc/profile\n', '').replace('. /etc/os-release\n', '')
        source = source.replace('/storage', str(self.storage))
        source = source.replace('/usr/config', str(self.root / 'usr/config'))
        prelude = f'''OS_NAME={shlex.quote(os_name)}
SETTINGS={shlex.quote(str(settings))}
MOUNT_ARGS={shlex.quote(str(self.root / 'mount.args'))}
get_setting() {{ sed -n "s/^$1=//p" "$SETTINGS"; }}
set_setting() {{ printf '%s=%s\\n' "$1" "$2" >> "$SETTINGS"; }}
log() {{ :; }}
mount() {{
  printf '%s\\n' "$@" > "$MOUNT_ARGS"
  if [ "$1" = --bind ]; then rmdir "$3" && ln -s "$2" "$3"; fi
}}
'''
        result = subprocess.run(['bash', '-s'], input=prelude + source +
                                f'\nstart_ms {shlex.quote(device)}\n',
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(settings.read_text(), original, 'Existing settings changed')
        return (self.root / 'mount.args').read_text().splitlines()

    def assert_visible(self, relative, contents='game fixture'):
        path = self.storage / 'roms' / relative
        self.assertTrue(path.is_file(), f'Game not visible through scan path: {relative}')
        self.assertEqual(path.read_text(), contents)

    def test_flat_card_games_are_visible_without_moving_them(self):
        game = self.game(self.external / 'gba/game.gba')
        self.run_mount()
        self.assert_visible('gba/game.gba')
        self.assertEqual(game.read_text(), 'game fixture')

    def test_empty_generated_nested_directories_do_not_hide_flat_games(self):
        self.game(self.external / 'gba/game.gba')
        for system in ['gba', 'snes']:
            (self.external / 'roms' / system).mkdir(parents=True)
        self.run_mount()
        self.assert_visible('gba/game.gba')

    def test_nested_card_games_remain_visible(self):
        self.game(self.external / 'roms/gba/game.gba')
        self.run_mount()
        self.assert_visible('gba/game.gba')

    def test_nested_support_files_do_not_hide_flat_games(self):
        self.game(self.external / 'roms/bios/firmware.bin')
        self.game(self.external / 'gba/game.gba')
        self.run_mount()
        self.assert_visible('gba/game.gba')

    def test_nested_library_wins_when_both_layouts_have_games(self):
        self.game(self.external / 'gba/game.gba', 'flat')
        self.game(self.external / 'roms/snes/game.sfc', 'nested')
        self.run_mount()
        self.assert_visible('snes/game.sfc', 'nested')
        self.assertFalse((self.storage / 'roms/gba/game.gba').exists())

    def test_empty_card_keeps_standard_nested_source(self):
        self.run_mount()
        self.assertEqual((self.storage / 'roms').resolve(), self.external / 'roms')

    def test_unrecognized_root_directory_keeps_standard_nested_source(self):
        self.game(self.external / 'unrelated/file.txt')
        self.run_mount()
        self.assertEqual((self.storage / 'roms').resolve(), self.external / 'roms')

    def test_missing_system_list_keeps_standard_nested_source(self):
        self.config.unlink()
        self.game(self.external / 'gba/game.gba')
        self.run_mount()
        self.assertEqual((self.storage / 'roms').resolve(), self.external / 'roms')

    def test_internal_storage_selection_is_preserved(self):
        self.game(self.internal / 'roms/gba/game.gba', 'internal')
        self.game(self.external / 'gba/game.gba', 'external')
        self.run_mount(device='internal')
        self.assert_visible('gba/game.gba', 'internal')

    def test_upstream_distribution_keeps_standard_nested_source(self):
        self.game(self.external / 'gba/game.gba')
        self.run_mount(os_name='ROCKNIX')
        self.assertEqual((self.storage / 'roms').resolve(), self.external / 'roms')

    def test_explicit_merged_storage_keeps_existing_overlay_paths(self):
        self.game(self.external / 'gba/game.gba')
        args = self.run_mount(merged=1)
        self.assertEqual(args[:4], ['overlay', '-t', 'overlay', '-o'])
        self.assertEqual(args[4], f'lowerdir={self.internal}/roms,'
                         f'upperdir={self.external}/roms,'
                         f'workdir={self.external}/.tmp/games-workdir')
        self.assertEqual(args[5], str(self.storage / 'roms'))

    def test_unsupported_overlay_bind_mode_can_use_flat_games(self):
        (self.storage / '.ms_unsupported').touch()
        self.game(self.external / 'gba/game.gba')
        self.run_mount(merged=1)
        self.assert_visible('gba/game.gba')

    def test_symlinked_system_directory_keeps_games_visible(self):
        self.config.write_text(self.config.read_text().replace('/gba</path>', '/gba/</path>'))
        self.game(self.external / 'actual-games/game.gba')
        (self.external / 'gba').symlink_to('actual-games', target_is_directory=True)
        self.run_mount()
        self.assert_visible('gba/game.gba')


if __name__ == '__main__':
    unittest.main()
