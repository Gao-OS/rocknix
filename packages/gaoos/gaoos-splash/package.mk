# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2025 ROCKNIX (https://github.com/ROCKNIX)
# Copyright (C) 2026 GaoOS (https://gaoos.dev)

PKG_NAME="gaoos-splash"
PKG_VERSION="9d295bcb74be2282e32c6b614efbea6036974ba8"
PKG_SHA256="ffa718a9bbcbf69857b91acf854abaf4cc00a7802f61d531341517546c26e635"
PKG_LICENSE="GPL"
PKG_SITE="https://github.com/ROCKNIX/rocknix-splash"
PKG_URL="https://github.com/ROCKNIX/rocknix-splash/archive/${PKG_VERSION}.tar.gz"
PKG_SOURCE_DIR="rocknix-splash-${PKG_VERSION}"
PKG_SOURCE_NAME="rocknix-splash-${PKG_VERSION}.tar.gz"
PKG_DEPENDS_INIT="toolchain Python3:host"
PKG_LONGDESC="GaoOS early framebuffer splash using ROCKNIX framebuffer and DT rotation support"
PKG_TOOLCHAIN="manual"
PKG_NEED_UNPACK="${DISTRO_DIR}/GaoOS/logos/gaoos-logo.png"

make_init() {
  # Compile only framebuffer/rotation support from the pinned upstream source.
  # The upstream main, SVG logo, and branding integrity check are not used.
  ${TOOLCHAIN}/bin/python3 ${PKG_DIR}/scripts/png-to-header.py \
    ${DISTRO_DIR}/GaoOS/logos/gaoos-logo.png ${PKG_BUILD}/logo.h
  ${CC} ${CFLAGS} -I${PKG_BUILD} -I${PKG_DIR}/src \
    ${PKG_DIR}/src/main.c ${PKG_DIR}/src/render.c \
    ${PKG_BUILD}/fbsplash.c ${PKG_BUILD}/dt_rotation.c \
    -o ${PKG_BUILD}/gaoos-splash ${LDFLAGS}
}

makeinstall_init() {
  mkdir -p ${INSTALL}/usr/bin
  cp ${PKG_BUILD}/gaoos-splash ${INSTALL}/usr/bin/
}
