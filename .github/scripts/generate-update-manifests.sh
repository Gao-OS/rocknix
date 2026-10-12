#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Generate OTA manifests from verified raw A/B payloads in the current directory.
# Usage: generate-update-manifests.sh <RELEASE_DATE> <TAG>
set -euo pipefail

RELEASE_DATE="${1:?Release date is required}"
TAG="${2:?Release tag is required}"
[[ "${RELEASE_DATE}" =~ ^[0-9]{8}$ ]] || { echo "ERROR: invalid release date ${RELEASE_DATE}" >&2; exit 1; }
shopt -s nullglob
payloads=(GaoOS-*.squashfs)
[ "${#payloads[@]}" -gt 0 ] || { echo "ERROR: no raw SquashFS OTA payloads found" >&2; exit 1; }

for payload in "${payloads[@]}"; do
  if [[ ! "${payload}" =~ ^GaoOS-([A-Za-z0-9_-]+)\.aarch64-([0-9]{8})\.squashfs$ ]]; then
    echo "ERROR: unexpected OTA payload name ${payload}" >&2
    exit 1
  fi
  DEVICE="${BASH_REMATCH[1]}"
  VERSION="${BASH_REMATCH[2]}"
  [ "$(head -c 4 "${payload}")" = "hsqs" ] || { echo "ERROR: ${payload} is not raw SquashFS" >&2; exit 1; }
  read -r EXPECTED_SHA _ < "${payload}.sha256"
  SHA=$(sha256sum "${payload}")
  SHA="${SHA%% *}"
  [ "${SHA}" = "${EXPECTED_SHA}" ] || { echo "ERROR: checksum mismatch for ${payload}" >&2; exit 1; }
  SIZE=$(stat -c %s "${payload}")
  jq -n --arg version "${VERSION}" --arg device "${DEVICE}" \
    --arg url "https://github.com/Gao-OS/rocknix/releases/download/${TAG}/${payload}" \
    --arg sha256 "${SHA}" --argjson size "${SIZE}" \
    --arg changelog "https://github.com/Gao-OS/rocknix/releases/tag/${TAG}" \
    '{version: $version, device: $device, url: $url, sha256: $sha256,
      size_bytes: $size, changelog_url: $changelog}' > "manifest-${DEVICE}.json"
  echo "Generated manifest-${DEVICE}.json"
done
