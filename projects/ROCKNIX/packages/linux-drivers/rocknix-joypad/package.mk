# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2024 ROCKNIX (https://github.com/ROCKNIX)

PKG_NAME="rocknix-joypad"
PKG_VERSION="d02ed13aae08113f6f9e0e9d699cb29bb3450fa2"
PKG_SHA256="89ade1769d6eb7b4f37265f26407a422cdbcf517487f80eb1c29964904ca138e"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/ROCKNIX/rocknix-joypad"
PKG_URL="${PKG_SITE}/archive/${PKG_VERSION}.tar.gz"
PKG_LONGDESC="rocknix-joypad: ROCKNIX joypad driver"
PKG_TOOLCHAIN="manual"
PKG_IS_KERNEL_PKG="yes"

if [ "${DISTRO}" = "GaoOS" -a "${DEVICE}" = "RK3566" ]; then
  # linux unpacks this source for its built-in early RGB30 driver. Avoid the
  # automatic kernel-package unpack dependency back to linux (a cycle).
  PKG_IS_KERNEL_PKG=""
  PKG_DEPENDS_TARGET="toolchain linux"
  PKG_NEED_UNPACK="${LINUX_DEPENDS}"
fi

pre_make_target() {
  unset LDFLAGS
}

make_target() {
  if [ "${DISTRO}" = "GaoOS" -a "${DEVICE}" = "RK3566" ]; then
    kernel_make -C $(kernel_path) M=${PKG_BUILD} rocknix-joypad.ko
  else
    kernel_make -C $(kernel_path) M=${PKG_BUILD}
  fi
}

makeinstall_target() {
  mkdir -p ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
    if [ "${DISTRO}" = "GaoOS" -a "${DEVICE}" = "RK3566" ]; then
      # singleadc is already built in; a duplicate module would re-register it.
      cp rocknix-joypad.ko ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
    else
      cp *.ko ${INSTALL}/$(get_full_module_dir)/${PKG_NAME}
    fi
}
