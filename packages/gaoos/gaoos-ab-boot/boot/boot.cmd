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
if load ${devtype} ${devnum}:1 ${scriptaddr} gaoos_boot.env; then
    env import -t ${scriptaddr} ${filesize} extra_cmdline gaoos_fdt gaoos_fdtdir gaoos_overlays
else
    echo "AB: boot metadata missing"
    exit
fi

# ── Load A/B state from boot partition ──────────────────────────
if load ${devtype} ${devnum}:1 ${scriptaddr} ab_state.env; then
    env import -t ${scriptaddr} ${filesize}
else
    echo "AB: ab_state.env not found, using defaults"
    setenv active_slot a
    setenv status_a good
    setenv status_b
    setenv retry_a 0
    setenv retry_b 0
    setenv max_retries 3
fi

# ── Resolve active slot → partition number ──────────────────────
setenv boot_slot ${active_slot}
if test "${boot_slot}" = "b"; then
    setenv boot_partnum 3
    setenv this_status ${status_b}
    setenv this_retry ${retry_b}
else
    setenv boot_slot a
    setenv boot_partnum 2
    setenv this_status ${status_a}
    setenv this_retry ${retry_a}
fi

# ── If active slot is BAD, try the other ────────────────────────
if test "${this_status}" = "bad"; then
    echo "AB: slot ${boot_slot} is BAD, switching..."
    if test "${boot_slot}" = "a"; then
        setenv boot_slot b
        setenv boot_partnum 3
        setenv this_status ${status_b}
        setenv this_retry ${retry_b}
    else
        setenv boot_slot a
        setenv boot_partnum 2
        setenv this_status ${status_a}
        setenv this_retry ${retry_a}
    fi
    # If BOTH are bad, last resort: slot a
    if test "${this_status}" = "bad"; then
        echo "AB: BOTH slots BAD -- last resort slot a"
        setenv boot_slot a
        setenv boot_partnum 2
        setenv this_status good
        setenv this_retry 0
        setenv status_a good
        setenv retry_a 0
        setenv active_slot a
    fi
fi

# ── If slot is PENDING, increment retry counter ────────────────
if test "${this_status}" = "pending"; then
    setexpr this_retry ${this_retry} + 1
    echo "AB: slot ${boot_slot} pending, retry ${this_retry}/${max_retries}"

    # Check if max retries exceeded
    setexpr retry_check ${max_retries} + 1
    if test ${this_retry} -ge ${retry_check}; then
        echo "AB: slot ${boot_slot} exceeded max retries, marking BAD"
        if test "${boot_slot}" = "a"; then
            setenv status_a bad
            setenv retry_a ${this_retry}
            # Fall back to slot b
            setenv boot_slot b
            setenv boot_partnum 3
            setenv active_slot b
        else
            setenv status_b bad
            setenv retry_b ${this_retry}
            # Fall back to slot a
            setenv boot_slot a
            setenv boot_partnum 2
            setenv active_slot a
        fi
    else
        # Update retry counter for current slot
        if test "${boot_slot}" = "a"; then
            setenv retry_a ${this_retry}
        else
            setenv retry_b ${this_retry}
        fi
    fi
fi

# ── Persist updated state back to FAT partition ────────────────
env export -t ${scriptaddr} active_slot status_a status_b retry_a retry_b max_retries
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
    setexpr gaoos_dtb sub "^.*/" "" "${fdtfile}"
    setenv gaoos_fdt ${gaoos_fdtdir}/${gaoos_dtb}
fi
if test -z "${gaoos_fdt}"; then
    echo "AB: no device tree selected"
    exit
fi
if load ${devtype} ${devnum}:1 ${fdt_addr_r} ${gaoos_fdt}; then
    fdt addr ${fdt_addr_r}
else
    echo "AB: failed to load ${gaoos_fdt}"
    exit
fi

# ── Load device tree overlays if present ───────────────────────
if test -n "${gaoos_overlays}"; then
    fdt resize 65536
    for overlay in ${gaoos_overlays}; do
        if load ${devtype} ${devnum}:1 ${fdtoverlay_addr_r} overlays/${overlay}; then
            fdt apply ${fdtoverlay_addr_r}
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
