&nbsp;&nbsp;<img src="https://raw.githubusercontent.com/Gao-OS/rocknix/gaoos/distributions/GaoOS/logos/gaoos-logo.png" width=192>

# GaoOS $DATE

GaoOS is a community-developed Linux distribution for handheld gaming devices, forked from [ROCKNIX](https://github.com/ROCKNIX/distribution). GaoOS adds an **A/B dual-slot boot system** for atomic, rollback-safe OTA updates — your device will never brick from a bad update.

## What's New in A/B Boot

- **Atomic updates**: Updates write to an inactive slot. Your running system is never modified.
- **Auto-rollback**: If a new version fails to boot 3 times, the device automatically reverts.
- **Manual rollback**: Run `gaoos-update rollback` to switch back at any time.
- **Safe by design**: Power loss during an update only affects the inactive slot.

## Installation (Fresh Flash)

Download the image for your device, decompress, and write to an SD card using [Balena Etcher](https://www.balena.io/etcher/) or `dd`.

| Device | Download | Documentation |
|--------|----------|---------------|
| **Anbernic RG353P/M/V/VS/PS, RG503, RGARC-D/S, Powkiddy RK2023, RGB10 Max 3, RGB30** | [GaoOS-RK3566.aarch64-$DATE-Generic.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-RK3566.aarch64-$DATE-Generic.img.gz) | [RK3566 docs](/documentation/PER_DEVICE_DOCUMENTATION/RK3566/) |
| **Powkiddy X55, X35S, Anbernic RG-DS** | [GaoOS-RK3566.aarch64-$DATE-Specific.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-RK3566.aarch64-$DATE-Specific.img.gz) | [RK3566 docs](/documentation/PER_DEVICE_DOCUMENTATION/RK3566/) |
| **Gameforce Ace, Orange Pi 5, Radxa Rock 5** | [GaoOS-RK3588.aarch64-$DATE.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-RK3588.aarch64-$DATE.img.gz) | [RK3588 docs](/documentation/PER_DEVICE_DOCUMENTATION/RK3588/) |
| **Anbernic RG351P/M/V, R33S/R35S/R36S, ODROID Go Advance/Super** | [GaoOS-RK3326.aarch64-$DATE.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-RK3326.aarch64-$DATE.img.gz) | [RK3326 docs](/documentation/PER_DEVICE_DOCUMENTATION/RK3326/) |
| **Anbernic RG552** | [GaoOS-RK3399.aarch64-$DATE.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-RK3399.aarch64-$DATE.img.gz) | [RK3399 docs](/documentation/PER_DEVICE_DOCUMENTATION/RK3399/) |
| **ODROID Go Ultra, RGB10 Max 3 Pro** | [GaoOS-S922X.aarch64-$DATE.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-S922X.aarch64-$DATE.img.gz) | [S922X docs](/documentation/PER_DEVICE_DOCUMENTATION/S922X/) |
| **Anbernic RG35XX+/H/SP, RG40XX, RGCUBEXX, RG34XX SP, RG28XX** | [GaoOS-H700.aarch64-$DATE.img.gz](https://github.com/Gao-OS/rocknix/releases/download/$DATE/GaoOS-H700.aarch64-$DATE.img.gz) | [H700 docs](/documentation/PER_DEVICE_DOCUMENTATION/H700/) |

## OTA Update (Existing GaoOS Users)

```bash
gaoos-update check        # check for new version
gaoos-update canary       # download, verify, write to inactive slot
reboot                    # boot into new version
gaoos-update status       # verify slot status
```

## Changelog

**Full Changelog**: https://github.com/Gao-OS/rocknix/compare/$LAST_TAG...$DATE

## Credits

GaoOS is built on [ROCKNIX](https://github.com/ROCKNIX/distribution), which is a fork of [JELOS](https://github.com/JustEnoughLinuxOS/distribution). All upstream licenses apply.
