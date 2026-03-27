# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 GaoOS (https://gaoos.dev)

PKG_NAME="gaoos-ab-boot"
PKG_VERSION="1.0.0"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/Gao-OS/rocknix"
PKG_URL=""
PKG_DEPENDS_TARGET="toolchain"
PKG_LONGDESC="GaoOS A/B dual-slot boot system with atomic, rollback-safe OTA updates"
PKG_TOOLCHAIN="manual"

makeinstall_target() {
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

  # Boot confirmation script
  cp ${PKG_DIR}/src/gaoos-boot-confirm ${INSTALL}/usr/bin/gaoos-boot-confirm
  chmod 755 ${INSTALL}/usr/bin/gaoos-boot-confirm

  # Systemd service for boot confirmation
  mkdir -p ${INSTALL}/usr/lib/systemd/system
  cp ${PKG_DIR}/system.d/gaoos-boot-confirm.service ${INSTALL}/usr/lib/systemd/system/

  # Default config
  mkdir -p ${INSTALL}/usr/share/gaoos
  cp ${PKG_DIR}/config/gaoos-ab.conf.default ${INSTALL}/usr/share/gaoos/gaoos-ab.conf.default
}

post_install() {
  enable_service gaoos-boot-confirm.service
}
