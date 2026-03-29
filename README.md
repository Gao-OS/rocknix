<img src="https://github.com/Gao-OS/rocknix/blob/gaoos/distributions/GaoOS/logos/gaoos-logo.png?raw=yes" width=192>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[![Latest Version](https://img.shields.io/github/release/Gao-OS/rocknix.svg?color=5588FF&label=latest%20version&style=flat-square)](https://github.com/Gao-OS/rocknix/releases/latest) [![Activity](https://img.shields.io/github/commit-activity/m/Gao-OS/rocknix?color=5588FF&style=flat-square)](https://github.com/Gao-OS/rocknix/commits) [![Pull Requests](https://img.shields.io/github/issues-pr-closed/Gao-OS/rocknix?color=5588FF&style=flat-square)](https://github.com/Gao-OS/rocknix/pulls)

---

GaoOS is an immutable Linux distribution for handheld gaming devices, forked from [ROCKNIX](https://github.com/ROCKNIX/distribution). GaoOS adds an **A/B dual-slot boot system** for atomic, rollback-safe OTA updates — your device will never brick from a bad update.

## What's Different from ROCKNIX

- **A/B boot**: Two system slots. Updates write to the inactive slot; your running system is never modified.
- **Auto-rollback**: If a new version fails to boot 3 times, the device automatically reverts to the last working version.
- **Hardware watchdog**: If boot hangs, the watchdog timer triggers a reboot into the recovery slot.
- **OTA updates**: Check, download, and apply updates from the command line or EmulationStation menu.
- **Delta updates**: Infrastructure for bsdiff-based delta patches to reduce download sizes (when available).

## Features

All ROCKNIX features are preserved:

* Integrated cross-device local and remote network play.
* In-game touch support on supported devices.
* Fine grain control for battery life or performance.
* Includes support for playing Music and Video.
* Bluetooth audio and controller support.
* Support for HDMI audio and video out, and USB audio.
* Device to device and device to cloud sync with Syncthing and rclone.
* VPN support with Wireguard, Tailscale, and ZeroTier.
* Includes built-in support for scraping and retroachievements.

## Supported Devices

| SoC | Devices |
|-----|---------|
| RK3566 | Anbernic RG353P/M/V/VS/PS, RG503, Powkiddy X55/X35S, RGB30 |
| RK3588 | Gameforce Ace, Orange Pi 5, Radxa Rock 5 |
| RK3326 | Anbernic RG351P/M/V, R33S/R35S/R36S, ODROID Go Advance/Super |
| RK3399 | Anbernic RG552 |
| S922X | ODROID Go Ultra, RGB10 Max 3 Pro |
| H700 | Anbernic RG35XX+/H/SP, RG40XX, RGCUBEXX, RG28XX |
| SM8250/SM8550/SM8650 | Qualcomm-based handhelds |

## Installation

Download the latest image for your device from [Releases](https://github.com/Gao-OS/rocknix/releases), decompress, and flash to an SD card:

```bash
gunzip GaoOS-RK3566.aarch64-*.img.gz
dd if=GaoOS-RK3566.aarch64-*.img of=/dev/sdX bs=4M status=progress
```

Or use [Balena Etcher](https://www.balena.io/etcher/) for a graphical tool.

## OTA Updates

```bash
gaoos-update check        # check for new version
gaoos-update canary       # download, verify, and stage to inactive slot
reboot                    # boot into the new version
gaoos-update status       # verify A/B slot status
gaoos-update rollback     # revert to previous version if needed
```

Updates are also accessible from the EmulationStation System Settings menu.

## Building from Source

```bash
# Enter dev environment (requires devenv + nix)
devenv shell

# Build for RK3566
make RK3566

# Or via Docker
make docker-RK3566
```

See [CLAUDE.md](CLAUDE.md) for full build system documentation.

## Licenses

**GaoOS** is a fork of [ROCKNIX](https://github.com/ROCKNIX/distribution), which is a fork of [JELOS](https://github.com/JustEnoughLinuxOS/distribution/). All upstream licenses apply.

You are free to:

- Share: copy and redistribute the material in any medium or format
- Adapt: remix, transform, and build upon the material

Under the following terms:

- Attribution: You must give appropriate credit, provide a link to the license, and indicate if changes were made.
- NonCommercial: You may not use the material for commercial purposes.
- ShareAlike: If you remix, transform, or build upon the material, you must distribute your contributions under the same license as the original.

### GaoOS Software

Copyright (C) 2026-present [Gao-OS](https://github.com/Gao-OS)

Original software and scripts developed by GaoOS are licensed under the terms of the [GNU GPL Version 2](https://choosealicense.com/licenses/gpl-2.0/). The full license can be found in this project's licenses folder.

### Bundled Works
All other software is provided under each component's respective license. These licenses can be found in the software sources or in this project's licenses folder.

## Credits

Like any Linux distribution, this project is not the work of one person. It is the work of many persons all over the world who have developed the open source bits without which this project could not exist. Special thanks to ROCKNIX, CoreELEC, LibreELEC, JELOS, and to developers and contributors across the open source community.
