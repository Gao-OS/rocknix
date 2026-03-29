# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 GaoOS (https://gaoos.dev)

PKG_NAME="gaoos-ab-boot"
PKG_VERSION="1.0.0"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/Gao-OS/rocknix"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain u-boot-tools:host"
PKG_LONGDESC="GaoOS A/B dual-slot boot system with atomic, rollback-safe OTA updates"
PKG_TOOLCHAIN="manual"

make_target() {
  # Compile boot.cmd → boot.scr for U-Boot A/B slot selection
  ${TOOLCHAIN}/bin/mkimage -C none -A ${TARGET_KERNEL_ARCH} -T script \
    -d ${PKG_DIR}/boot/boot.cmd ${PKG_BUILD}/boot.scr
}

makeinstall_target() {
  # A/B boot script (for bootloader share dir — copied to boot partition by mkimage/update.sh)
  mkdir -p ${INSTALL}/usr/share/bootloader
  cp ${PKG_BUILD}/boot.scr ${INSTALL}/usr/share/bootloader/boot.scr

  # Default ab_state.env template
  mkdir -p ${INSTALL}/usr/share/gaoos
  cp ${PKG_DIR}/boot/ab_state.env.default ${INSTALL}/usr/share/gaoos/ab_state.env.default

  # gaoos-update CLI
  mkdir -p ${INSTALL}/usr/bin
  cp ${PKG_DIR}/src/gaoos-update ${INSTALL}/usr/bin/gaoos-update
  chmod 755 ${INSTALL}/usr/bin/gaoos-update

  # ES integration wrappers — replace upstream update scripts
  # These override rocknix-update, updatecheck, and system-upgrade
  # so EmulationStation calls gaoos-update instead of the old tar-based flow
  cp ${PKG_DIR}/src/updatecheck ${INSTALL}/usr/bin/updatecheck
  chmod 755 ${INSTALL}/usr/bin/updatecheck

  cp ${PKG_DIR}/src/system-upgrade ${INSTALL}/usr/bin/system-upgrade
  chmod 755 ${INSTALL}/usr/bin/system-upgrade

  cp ${PKG_DIR}/src/rocknix-update ${INSTALL}/usr/bin/rocknix-update
  chmod 755 ${INSTALL}/usr/bin/rocknix-update

  # ES integration — additional menu actions (FR9.2)
  cp ${PKG_DIR}/src/gaoos-reboot-update ${INSTALL}/usr/bin/gaoos-reboot-update
  chmod 755 ${INSTALL}/usr/bin/gaoos-reboot-update

  cp ${PKG_DIR}/src/gaoos-rollback ${INSTALL}/usr/bin/gaoos-rollback
  chmod 755 ${INSTALL}/usr/bin/gaoos-rollback

  cp ${PKG_DIR}/src/gaoos-status-dialog ${INSTALL}/usr/bin/gaoos-status-dialog
  chmod 755 ${INSTALL}/usr/bin/gaoos-status-dialog

  # Partition creation tool (FR1.4)
  cp ${PKG_DIR}/src/gaoos-partition.sh ${INSTALL}/usr/bin/gaoos-partition
  chmod 755 ${INSTALL}/usr/bin/gaoos-partition

  # Boot confirmation script
  cp ${PKG_DIR}/src/gaoos-boot-confirm ${INSTALL}/usr/bin/gaoos-boot-confirm
  chmod 755 ${INSTALL}/usr/bin/gaoos-boot-confirm

  # Boot watchdog feeder (M8)
  cp ${PKG_DIR}/src/gaoos-watchdog ${INSTALL}/usr/bin/gaoos-watchdog
  chmod 755 ${INSTALL}/usr/bin/gaoos-watchdog

  # Systemd services
  mkdir -p ${INSTALL}/usr/lib/systemd/system
  cp ${PKG_DIR}/system.d/gaoos-boot-confirm.service ${INSTALL}/usr/lib/systemd/system/
  cp ${PKG_DIR}/system.d/gaoos-watchdog.service ${INSTALL}/usr/lib/systemd/system/

  # Default config
  mkdir -p ${INSTALL}/usr/share/gaoos
  cp ${PKG_DIR}/config/gaoos-ab.conf.default ${INSTALL}/usr/share/gaoos/gaoos-ab.conf.default
}

post_install() {
  enable_service gaoos-boot-confirm.service
  enable_service gaoos-watchdog.service
}
