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
save ${devtype} ${devnum}:1 ${scriptaddr} ab_state.env ${filesize}

# ── Load kernel ────────────────────────────────────────────────
echo "AB: booting slot ${boot_slot} (partition ${boot_partnum})"
load ${devtype} ${devnum}:1 ${kernel_addr_r} KERNEL

# ── Load device tree ───────────────────────────────────────────
if test -d ${devtype} ${devnum}:1 device_trees; then
    if test -n "${fdtfile}"; then
        load ${devtype} ${devnum}:1 ${fdt_addr_r} device_trees/${fdtfile}
    else
        echo "AB: fdtfile not set, trying FDTDIR via sysboot fallback"
        # Fall back to extlinux for DTB selection on generic devices
        sysboot ${devtype} ${devnum}:1 any ${scriptaddr} /extlinux/extlinux.conf
    fi
else
    echo "AB: no device_trees dir, trying sysboot fallback"
    sysboot ${devtype} ${devnum}:1 any ${scriptaddr} /extlinux/extlinux.conf
fi

# ── Load device tree overlays if present ───────────────────────
if test -e ${devtype} ${devnum}:1 overlays; then
    fdt addr ${fdt_addr_r}
    fdt resize 65536
    for overlay in ${dto}; do
        echo "AB: applying overlay ${overlay}"
        load ${devtype} ${devnum}:1 ${fdtoverlay_addr_r} overlays/${overlay}
        fdt apply ${fdtoverlay_addr_r}
    done
fi

# ── Construct boot arguments ──────────────────────────────────
setenv bootargs "boot=LABEL=${bootlabel} disk=LABEL=${storagelabel} gaoos.slot=${boot_slot} ${extra_cmdline}"

# ── Boot ──────────────────────────────────────────────────────
booti ${kernel_addr_r} - ${fdt_addr_r}
