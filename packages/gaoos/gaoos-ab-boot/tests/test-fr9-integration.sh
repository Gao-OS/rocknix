#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Copyright (C) 2026 GaoOS (https://gaoos.dev)
#
# Test suite for FR9 EmulationStation integration
# Run from the repo root: bash packages/gaoos/gaoos-ab-boot/tests/test-fr9-integration.sh

PASS=0
FAIL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PKG_DIR="$(dirname "${SCRIPT_DIR}")"
SRC_DIR="${PKG_DIR}/src"

pass() { echo "  PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL+1)); }

echo "=== FR9 EmulationStation Integration Tests ==="
echo ""

# ── FR9.1: No competing update mechanisms ──
echo "FR9.1: Replace ROCKNIX update flow"

# rocknix-update shim redirects to gaoos-update
if grep -q 'gaoos-update' "${SRC_DIR}/rocknix-update"; then
  pass "rocknix-update shim redirects to gaoos-update"
else
  fail "rocknix-update shim does not reference gaoos-update"
fi

# updatecheck wrapper calls gaoos-update check
if grep -q 'gaoos-update check' "${SRC_DIR}/updatecheck"; then
  pass "updatecheck wrapper calls gaoos-update check"
else
  fail "updatecheck wrapper does not call gaoos-update check"
fi

# system-upgrade wrapper calls gaoos-update canary
if grep -q 'gaoos-update canary' "${SRC_DIR}/system-upgrade"; then
  pass "system-upgrade wrapper calls gaoos-update canary"
else
  fail "system-upgrade wrapper does not call gaoos-update canary"
fi

echo ""

# ── FR9.2: All ES menu scripts exist ──
echo "FR9.2: ES Software Update menu scripts"

for script in updatecheck system-upgrade gaoos-reboot-update gaoos-rollback gaoos-status-dialog; do
  if [ -f "${SRC_DIR}/${script}" ]; then
    pass "${script} exists"
  else
    fail "${script} missing"
  fi
done

# package.mk installs all scripts
if grep -q 'gaoos-reboot-update' "${PKG_DIR}/package.mk" && \
   grep -q 'gaoos-rollback' "${PKG_DIR}/package.mk" && \
   grep -q 'gaoos-status-dialog' "${PKG_DIR}/package.mk"; then
  pass "package.mk installs all FR9.2 scripts"
else
  fail "package.mk missing FR9.2 script installs"
fi

# gaoos-reboot-update checks for pending state before rebooting
if grep -q 'pending' "${SRC_DIR}/gaoos-reboot-update"; then
  pass "gaoos-reboot-update checks for pending state"
else
  fail "gaoos-reboot-update does not check pending state"
fi

# gaoos-rollback calls gaoos-update rollback
if grep -q 'gaoos-update rollback' "${SRC_DIR}/gaoos-rollback"; then
  pass "gaoos-rollback calls gaoos-update rollback"
else
  fail "gaoos-rollback does not call gaoos-update rollback"
fi

# gaoos-status-dialog calls gaoos-update status
if grep -q 'gaoos-update status' "${SRC_DIR}/gaoos-status-dialog"; then
  pass "gaoos-status-dialog calls gaoos-update status"
else
  fail "gaoos-status-dialog does not call gaoos-update status"
fi

echo ""

# ── FR9.3: Tar detection disabled ──
echo "FR9.3: Legacy tar update detection disabled"

INIT_SCRIPT="$(cd "${PKG_DIR}/../../.." && pwd)/projects/ROCKNIX/packages/sysutils/busybox/scripts/init"
if [ -f "${INIT_SCRIPT}" ]; then
  if grep -q 'gaoos-update' "${INIT_SCRIPT}"; then
    pass "init script has gaoos-update guard in check_update()"
  else
    fail "init script missing gaoos-update guard"
  fi

  if grep -q 'FR9.3' "${INIT_SCRIPT}"; then
    pass "init script guard references FR9.3"
  else
    fail "init script guard does not reference FR9.3"
  fi
else
  fail "busybox init script not found at expected path"
fi

echo ""

# ── FR9.4: Progress reporting ──
echo "FR9.4: Update progress reporting"

if grep -q 'PROGRESS_FILE' "${SRC_DIR}/gaoos-update"; then
  pass "gaoos-update writes to PROGRESS_FILE"
else
  fail "gaoos-update does not use PROGRESS_FILE"
fi

if grep -q 'PROGRESS_FILE' "${SRC_DIR}/system-upgrade"; then
  pass "system-upgrade manages PROGRESS_FILE lifecycle"
else
  fail "system-upgrade does not manage PROGRESS_FILE"
fi

echo ""

# ── Script syntax validation ──
echo "Script syntax validation"

for script in gaoos-update rocknix-update updatecheck system-upgrade gaoos-reboot-update gaoos-rollback gaoos-status-dialog gaoos-boot-confirm; do
  if bash -n "${SRC_DIR}/${script}" 2>/dev/null; then
    pass "${script} has valid bash syntax"
  else
    fail "${script} has syntax errors"
  fi
done

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed ==="
exit ${FAIL}
