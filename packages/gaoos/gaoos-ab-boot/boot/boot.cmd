# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 GaoOS (https://gaoos.dev)
#
# GaoOS A/B Boot Script for U-Boot
# Compiled to boot.scr via: mkimage -C none -A arm64 -T script -d boot.cmd boot.scr
#
# Reads ab_state.env from the boot FAT partition, selects the active SquashFS
# slot (partition 2 or 3), handles retry counting and auto-rollback, then boots.

echo "GaoOS A/B Boot"

setenv bootlabel "GAOOS"
setenv storagelabel "STORAGE"

# Import build metadata without changing the board-selected Generic fdtfile.
setenv extra_cmdline
setenv gaoos_fdt
setenv gaoos_fdtdir
setenv gaoos_overlays
if load ${devtype} ${devnum}:1 ${scriptaddr} gaoos_boot.env; then
    if env import -t ${scriptaddr} ${filesize} extra_cmdline gaoos_fdt gaoos_fdtdir gaoos_overlays; then
        echo "AB: boot metadata loaded"
    else
        echo "AB: boot metadata import failed"
        exit
    fi
else
    echo "AB: boot metadata missing"
    exit
fi

# Reset imported fields so missing or partial state cannot inherit saved U-Boot env.
setenv active_slot
setenv status_a
setenv status_b
setenv retry_a
setenv retry_b
setenv max_retries
setenv ab_state_valid yes
if load ${devtype} ${devnum}:1 ${scriptaddr} ab_state.env; then
    if env import -t ${scriptaddr} ${filesize} active_slot status_a status_b retry_a retry_b max_retries; then
        echo "AB: state loaded"
    else
        setenv ab_state_valid no
    fi
else
    setenv ab_state_valid no
fi

# Reject malformed counters before arithmetic. The supported retry policy is three
# attempts; exhausted counters remain at three when the slot is marked bad.
if test "${active_slot}" != "a" -a "${active_slot}" != "b"; then
    setenv ab_state_valid no
fi
for slot_status in "${status_a}" "${status_b}"; do
    if test -n "${slot_status}" -a "${slot_status}" != "good" -a "${slot_status}" != "pending" -a "${slot_status}" != "bad"; then
        setenv ab_state_valid no
    fi
done
for slot_retry in "${retry_a}" "${retry_b}"; do
    if test "${slot_retry}" != "0" -a "${slot_retry}" != "1" -a "${slot_retry}" != "2" -a "${slot_retry}" != "3"; then
        setenv ab_state_valid no
    fi
done
if test "${max_retries}" != "3"; then
    setenv ab_state_valid no
fi
if test "${ab_state_valid}" != "yes"; then
    echo "AB: missing or invalid state; recovering with counted slot A"
    setenv active_slot a
    setenv status_a good
    setenv status_b
    setenv retry_a 0
    setenv retry_b 0
    setenv max_retries 3
fi

# Mark exhausted trials bad before choosing a usable slot. Never promote a bad
# slot or boot the factory-empty B partition as an unchecked fallback.
if test "${status_a}" = "pending" -a "${retry_a}" = "3"; then
    setenv status_a bad
fi
if test "${status_b}" = "pending" -a "${retry_b}" = "3"; then
    setenv status_b bad
fi
setenv boot_slot ${active_slot}
if test "${boot_slot}" = "a"; then
    if test "${status_a}" != "good" -a "${status_a}" != "pending"; then
        setenv boot_slot b
    fi
else
    if test "${status_b}" != "good" -a "${status_b}" != "pending"; then
        setenv boot_slot a
    fi
fi
if test "${boot_slot}" = "a"; then
    setenv this_status ${status_a}
    setenv this_retry ${retry_a}
    setenv boot_partnum 2
else
    setenv this_status ${status_b}
    setenv this_retry ${retry_b}
    setenv boot_partnum 3
fi
if test "${this_status}" != "good" -a "${this_status}" != "pending"; then
    echo "AB: no usable slot; recovery/reflash required"
    if env export -t ${scriptaddr} active_slot status_a status_b retry_a retry_b max_retries; then
        if save ${devtype} ${devnum}:1 ${scriptaddr} ab_state.env ${filesize}; then
            echo "AB: failed state persisted"
        else
            echo "AB: failed state could not be persisted"
        fi
    fi
    exit
fi

# Every boot is a trial until userspace health checks confirm it. This includes
# formerly good slots, so a damaged rootfs cannot cause an infinite boot loop.
if test "${this_status}" = "good"; then
    setenv this_retry 0
fi
setexpr this_retry ${this_retry} + 1
setenv active_slot ${boot_slot}
if test "${boot_slot}" = "a"; then
    setenv status_a pending
    setenv retry_a ${this_retry}
else
    setenv status_b pending
    setenv retry_b ${this_retry}
fi
echo "AB: slot ${boot_slot} attempt ${this_retry}/${max_retries}"
if env export -t ${scriptaddr} active_slot status_a status_b retry_a retry_b max_retries; then
    echo "AB: state exported"
else
    echo "AB: state export failed; refusing an uncounted boot"
    exit
fi
if save ${devtype} ${devnum}:1 ${scriptaddr} ab_state.env ${filesize}; then
    echo "AB: state persisted"
else
    echo "AB: failed to persist state; refusing an uncounted boot"
    exit
fi

# ── Load kernel ────────────────────────────────────────────────
echo "AB: booting slot ${boot_slot} (partition ${boot_partnum})"
if load ${devtype} ${devnum}:1 ${kernel_addr_r} KERNEL; then
    echo "AB: kernel loaded"
else
    echo "AB: failed to load kernel"
    exit
fi

# ── Load device tree ───────────────────────────────────────────
if test -z "${gaoos_fdt}"; then
    # Generic U-Boot detects the board; accept both bare and vendor-prefixed names.
    if test -z "${gaoos_fdtdir}" -o -z "${fdtfile}"; then
        echo "AB: Generic DTB directory or board selection missing"
        exit
    fi
    setexpr gaoos_dtb sub "^.*/" "" "${fdtfile}"
    setenv gaoos_fdt ${gaoos_fdtdir}/${gaoos_dtb}
fi
if test -z "${gaoos_fdt}"; then
    echo "AB: no device tree selected"
    exit
fi
if load ${devtype} ${devnum}:1 ${fdt_addr_r} ${gaoos_fdt}; then
    if fdt addr ${fdt_addr_r}; then
        echo "AB: device tree ${gaoos_fdt} loaded"
    else
        echo "AB: invalid device tree ${gaoos_fdt}"
        exit
    fi
else
    echo "AB: failed to load ${gaoos_fdt}"
    exit
fi

# ── Load device tree overlays if present ───────────────────────
if test -n "${gaoos_overlays}"; then
    if fdt resize 65536; then
        echo "AB: preparing overlays"
    else
        echo "AB: cannot resize device tree"
        exit
    fi
    for overlay in ${gaoos_overlays}; do
        if load ${devtype} ${devnum}:1 ${fdtoverlay_addr_r} overlays/${overlay}; then
            if fdt apply ${fdtoverlay_addr_r}; then
                echo "AB: applied overlay ${overlay}"
            else
                echo "AB: invalid overlay ${overlay}"
                exit
            fi
        else
            echo "AB: failed to load overlay ${overlay}"
            exit
        fi
    done
fi

# ── Resolve boot device name for Linux ─────────────────────────
# Map U-Boot device type/number to Linux block device name
if test "${devtype}" = "mmc"; then
    setenv gaoos_dev "mmcblk${devnum}"
elif test "${devtype}" = "scsi" -o "${devtype}" = "usb"; then
    setenv gaoos_dev "sda"
else
    setenv gaoos_dev "mmcblk0"
fi

# ── Construct boot arguments ──────────────────────────────────
setenv bootargs "boot=LABEL=${bootlabel} disk=LABEL=${storagelabel} gaoos.slot=${boot_slot} gaoos.dev=${gaoos_dev} ${extra_cmdline}"

# ── Boot ──────────────────────────────────────────────────────
booti ${kernel_addr_r} - ${fdt_addr_r}
