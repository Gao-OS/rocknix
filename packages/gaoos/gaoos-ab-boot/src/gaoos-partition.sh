#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 GaoOS (https://gaoos.dev)
#
# gaoos-partition.sh — Create 4-partition A/B boot layout on an SD card
#
# Usage: gaoos-partition.sh /dev/sdX
#
# WARNING: This will DESTROY all data on the target device!

set -euo pipefail

BOOT_SIZE_MB=256
SLOT_SIZE_MB=768
STORAGE_SIZE_MB=32
PART_START_SECTOR=32768  # 16MB offset for U-Boot

usage() {
  echo "Usage: $(basename "$0") <device>"
  echo ""
  echo "Creates the GaoOS 4-partition A/B boot layout:"
  echo "  p1: boot    (FAT32, ${BOOT_SIZE_MB}MB) — kernel, dtb, boot.scr, ab_state.env"
  echo "  p2: slot_a  (raw, ${SLOT_SIZE_MB}MB)   — SquashFS system image A"
  echo "  p3: slot_b  (raw, ${SLOT_SIZE_MB}MB)   — SquashFS system image B"
  echo "  p4: storage (ext4)                      — user data (fills remaining space)"
  echo ""
  echo "Example: $(basename "$0") /dev/sdb"
  echo ""
  echo "WARNING: All data on the target device will be destroyed!"
  exit 1
}

die() { echo "ERROR: $*" >&2; exit 1; }

[ $# -eq 1 ] || usage
DEVICE="$1"

# Safety checks
[ -b "${DEVICE}" ] || die "${DEVICE} is not a block device"
[ "$(id -u)" = "0" ] || die "Must run as root"

# Prevent operating on mounted devices
if mount | grep -q "^${DEVICE}"; then
  die "${DEVICE} has mounted partitions. Unmount them first."
fi

# Confirm
echo "This will DESTROY all data on ${DEVICE}!"
echo ""
echo "Partition layout:"
echo "  p1: boot    FAT32  ${BOOT_SIZE_MB}MB"
echo "  p2: slot_a  raw    ${SLOT_SIZE_MB}MB"
echo "  p3: slot_b  raw    ${SLOT_SIZE_MB}MB"
echo "  p4: storage ext4   (remaining space)"
echo ""
read -p "Type YES to continue: " CONFIRM
[ "${CONFIRM}" = "YES" ] || die "Aborted."

echo "Creating partition table..."
parted -s "${DEVICE}" mklabel msdos

# Calculate partition boundaries (in sectors, 512 bytes each)
BOOT_END=$((PART_START_SECTOR + (BOOT_SIZE_MB * 1024 * 1024 / 512) - 1))
SLOT_A_START=$((BOOT_END + 1))
SLOT_A_END=$((SLOT_A_START + (SLOT_SIZE_MB * 1024 * 1024 / 512) - 1))
SLOT_B_START=$((SLOT_A_END + 1))
SLOT_B_END=$((SLOT_B_START + (SLOT_SIZE_MB * 1024 * 1024 / 512) - 1))
STORAGE_START=$((SLOT_B_END + 1))

echo "Creating partitions..."
parted -s "${DEVICE}" -a min unit s \
  mkpart primary fat32 ${PART_START_SECTOR} ${BOOT_END} \
  mkpart primary ${SLOT_A_START} ${SLOT_A_END} \
  mkpart primary ${SLOT_B_START} ${SLOT_B_END} \
  mkpart primary ext4 ${STORAGE_START} 100%
parted -s "${DEVICE}" set 1 boot on

# Wait for kernel to re-read partition table
sync
partprobe "${DEVICE}" 2>/dev/null || true
sleep 1

# Determine partition device naming
if [[ "${DEVICE}" =~ [0-9]$ ]]; then
  PART_PREFIX="${DEVICE}p"
else
  PART_PREFIX="${DEVICE}"
fi

echo "Formatting boot partition (p1)..."
mkfs.vfat -F 32 -n "GAOOS" "${PART_PREFIX}1"

echo "Formatting storage partition (p4)..."
mkfs.ext4 -F -L "STORAGE" -m 0 "${PART_PREFIX}4"

# Write default ab_state.env to boot partition
echo "Writing default ab_state.env..."
MOUNT_TMP=$(mktemp -d)
mount "${PART_PREFIX}1" "${MOUNT_TMP}"
cat > "${MOUNT_TMP}/ab_state.env" <<EOF
active_slot=a
status_a=good
status_b=
retry_a=0
retry_b=0
max_retries=3
EOF
umount "${MOUNT_TMP}"
rmdir "${MOUNT_TMP}"

echo ""
echo "Done! Partition layout:"
parted -s "${DEVICE}" unit MB print
echo ""
echo "Next steps:"
echo "  1. Write U-Boot to sector 64: dd if=uboot.bin of=${DEVICE} bs=512 seek=64"
echo "  2. Copy kernel + dtb + boot.scr to p1"
echo "  3. Write SquashFS to p2: dd if=system.squashfs of=${PART_PREFIX}2 bs=4M"
