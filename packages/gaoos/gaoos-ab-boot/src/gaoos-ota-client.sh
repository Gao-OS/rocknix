#!/bin/bash
# SPDX-License-Identifier: GPL-2.0
# Loaded by gaoos-update and gaoos-ota-report. Remote paths never use unsigned sidecars.

OTA_HELPER=/usr/bin/gaoos-ota-helper
OTA_LOCK=/run/gaoos-ota.lock

ota_load_config() {
  local config=/storage/.config/gaoos-ab.conf
  local defaults=/usr/share/gaoos/gaoos-ab.conf.default
  if [ -f "${config}" ]; then
    . "${config}"
  elif [ -f "${defaults}" ]; then
    . "${defaults}"
  fi
}

ota_paths() {
  OTA_DIR="${STAGING_DIR}/.ota"
  OTA_EVENTS="${OTA_DIR}/events"
  OTA_JOURNAL="${OTA_DIR}/install"
  UPDATE_TOKEN_FILE="${UPDATE_TOKEN_FILE:-/storage/.config/gaoos-ota/device-token}"
  UPDATE_TRUST_FILE="${UPDATE_TRUST_FILE:-/storage/.config/gaoos-ota/trusted-keys.json}"
}

ota_lock() {
  umask 077
  mkdir -p "${OTA_EVENTS}" || return 1
  exec 8>"${OTA_LOCK}" || return 1
  flock -n 8
}

ota_sequence() {
  if [ -e /etc/gaoos-ota-sequence ]; then
    local sequence
    sequence=$(cat /etc/gaoos-ota-sequence) || return 1
    [[ "${sequence}" =~ ^(0|[1-9][0-9]{0,15})$ ]] || return 1
    printf '%s\n' "${sequence}"
  else
    # A manually flashed baseline predating the sequence field.
    printf '0\n'
  fi
}

ota_boot_arguments() {
  local compat kernel dtb fdt_path version sequence running inactive
  compat=$(tr '\000' '\n' </proc/device-tree/compatible | head -n1) || return 1
  [ "${compat}" = 'powkiddy,rgb30' ] && [ "$(uname -m)" = aarch64 ] || return 1

  running=$(get_running_slot) || return 1
  inactive=$(get_inactive_slot "${running}") || return 1
  slot_partitions "${running}" "${inactive}" >/dev/null || return 1

  fdt_path=$(awk '{
    for(i=1;i<=NF;i++) if($i ~ /^gaoos\.fdt=/) {n++; file=substr($i,11)}
    if(n!=1) exit 1
    print file
  }' /proc/cmdline) || return 1
  [ "${fdt_path}" = device_trees/rk3566-powkiddy-rgb30.dtb ] || return 1

  # Only the standard RGB30 DTB selection is qualified. The kernel includes
  # the initramfs, and the Generic boot environment must select no overlays.
  awk '
    {sub(/\r$/, "")}
    /^gaoos_fdtdir=device_trees$/ {dir++; next}
    /^gaoos_fdt=$/ {fdt++; next}
    /^gaoos_overlays=$/ {overlay++; next}
    /^extra_cmdline=/ {cmdline++; next}
    {invalid=1}
    END {exit (invalid || dir!=1 || fdt!=1 || overlay!=1 || cmdline!=1)}
  ' /flash/gaoos_boot.env || return 1
  kernel=$(sha256sum /flash/KERNEL | awk '{print $1}') || return 1
  dtb=$(sha256sum "/flash/${fdt_path}" | awk '{print $1}') || return 1
  version=$(get_version) || return 1
  sequence=$(ota_sequence) || return 1
  OTA_BOOT_ARGS=(--version "${version}" --sequence "${sequence}" --kernel "${kernel}" --dtb "${dtb}")
}

ota_request() {
  local command="$1"
  shift
  local reply rc
  if reply=$("${OTA_HELPER}" "${command}" --api "${UPDATE_API_URL}" --token-file "${UPDATE_TOKEN_FILE}" \
    --trust-file "${UPDATE_TRUST_FILE}" "${OTA_BOOT_ARGS[@]}" "$@"); then
    IFS=$'\t' read -r OTA_VERSION OTA_SHA OTA_SIZE OTA_SEQUENCE OTA_RELEASE <<<"${reply}"
    ota_uuid "${OTA_RELEASE}" && [[ "${OTA_SIZE}" =~ ^[1-9][0-9]{0,9}$ ]] &&
      [[ "${OTA_SHA}" =~ ^[0-9a-f]{64}$ ]] && [[ "${OTA_SEQUENCE}" =~ ^[1-9][0-9]{0,15}$ ]]
  else
    rc=$?
    return "${rc}"
  fi
}

ota_uuid() { [[ "$1" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]; }

ota_new_id() {
  local value
  read -r value </proc/sys/kernel/random/uuid || return 1
  ota_uuid "${value}" || return 1
  printf '%s\n' "${value}"
}

ota_atomic() {
  local path="$1" temp
  temp=$(mktemp "${path}.tmp.XXXXXX") || return 1
  if cat >"${temp}" && sync && mv -f -- "${temp}" "${path}" && sync; then
    return 0
  fi
  rm -f -- "${temp}"
  return 1
}

ota_attempt_id() {
  local file="$1" attempt
  if [ -f "${file}" ] && read -r attempt <"${file}" && ota_uuid "${attempt}"; then
    printf '%s\n' "${attempt}"
    return 0
  fi
  attempt=$(ota_new_id) || return 1
  printf '%s\n' "${attempt}" | ota_atomic "${file}" || return 1
  printf '%s\n' "${attempt}"
}

ota_event() {
  local release="$1" attempt="$2" type="$3" detail="${4:-}" path event extra=""
  ota_uuid "${release}" && ota_uuid "${attempt}" || return 1
  case "${type}:${detail}" in
    downloaded:|staged:|booted:|confirmed:|rolled_back:) ;;
    failed:download|failed:validation|failed:write|failed:boot) extra=",\"detail\":\"${detail}\"" ;;
    *) return 1 ;;
  esac
  path="${OTA_EVENTS}/${attempt}-${type}.json"
  [ ! -f "${path}" ] || return 0

  # A stable ID also deduplicates a journal event replay after a successful send.
  local digest
  digest=$(printf 'gaoos-event-v1:%s:%s' "${attempt}" "${type}" | sha256sum) || return 1
  event="${digest:0:8}-${digest:8:4}-${digest:12:4}-${digest:16:4}-${digest:20:12}"
  printf '{"event_id":"%s","release_id":"%s","attempt_id":"%s","type":"%s"%s}\n' \
    "${event}" "${release}" "${attempt}" "${type}" "${extra}" | ota_atomic "${path}"
}

ota_boot_id() { cat /proc/sys/kernel/random/boot_id; }

ota_write_journal() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$@" | ota_atomic "${OTA_JOURNAL}"
}

ota_state_value() {
  local key="$1"
  awk -F= -v key="${key}" '$1==key {n++; value=$2} END {if(n!=1) exit 1; print value}' /flash/ab_state.env
}

ota_reconcile() {
  [ -f "${OTA_JOURNAL}" ] || return 0
  local release attempt source target version sequence boot_id phase running active status retries
  IFS=$'\t' read -r release attempt source target version sequence boot_id phase <"${OTA_JOURNAL}" || return 1
  ota_uuid "${release}" && ota_uuid "${attempt}" && ota_uuid "${boot_id}" || return 1
  case "${source}:${target}" in a:b|b:a) ;; *) return 1 ;; esac
  case "${phase}" in intent|staged) ;; *) return 1 ;; esac

  # A missing or ambiguous boot claim must never create a success event.
  running=$(awk '{
    for (i=1; i<=NF; i++) if ($i ~ /^gaoos\.slot=/) {n++; slot=substr($i,12)}
    if (n!=1 || (slot!="a" && slot!="b")) exit 1
    print slot
  }' /proc/cmdline) || return 1
  active=$(ota_state_value active_slot) || return 1
  status=$(ota_state_value "status_${target}") || return 1
  retries=$(ota_state_value "retry_${target}") || return 1

  if [ "${running}" = "${target}" ] && [ "${active}" = "${target}" ] && [ "${status}" = good ] &&
    [ "$(cat /etc/gaoos-version)" = "${version}" ] && [ "$(ota_sequence)" = "${sequence}" ]; then
    ota_event "${release}" "${attempt}" staged &&
      ota_event "${release}" "${attempt}" booted &&
      ota_event "${release}" "${attempt}" confirmed &&
      rm -f -- "${OTA_JOURNAL}"
  elif [ "${running}" = "${source}" ] && [ "${active}" = "${source}" ] && [ "${status}" = bad ] &&
    [[ "${retries}" =~ ^[1-3]$ ]]; then
    ota_event "${release}" "${attempt}" staged &&
      ota_event "${release}" "${attempt}" rolled_back &&
      rm -f -- "${OTA_JOURNAL}"
  elif [ "${phase}" = intent ] && [ "${running}" = "${source}" ] && [ "${active}" = "${source}" ] &&
    [ "$(ota_boot_id)" != "${boot_id}" ]; then
    ota_event "${release}" "${attempt}" failed write && rm -f -- "${OTA_JOURNAL}"
  elif [ "${active}" = "${target}" ]; then
    ota_event "${release}" "${attempt}" staged
  fi
}

cmd_ota_check() (
  ota_load_config || die "Cannot load OTA configuration"
  [ -n "${UPDATE_API_URL:-}" ] || { printf 'no\n'; return 0; }
  ota_paths
  local rc
  ota_boot_arguments || die "Cannot verify the running RGB30 boot components"
  if ota_request discover; then
    printf '%s\n' "${OTA_VERSION}"
  else
    rc=$?
    printf 'no\n'
    [ "${rc}" -eq 3 ] || return "${rc}"
  fi
)

ota_download_locked() {
  local url="${1:-}" digest="${2:-}" rc release attempt_file attempt stale
  ota_boot_arguments || die "Cannot verify the running RGB30 boot components"
  progress 0 "Checking and downloading signed update..."
  if ota_request discover; then
    release="${OTA_RELEASE}"
  else
    rc=$?
    [ "${rc}" -ne 3 ] || log "No update available."
    return "${rc}"
  fi

  attempt_file="${OTA_DIR}/attempt-${release}"
  # Only retain the current signed download. An interrupted helper can leave
  # an unpublished partial file, but it never becomes an installable path.
  for stale in "${OTA_DIR}"/release-*.squashfs "${OTA_DIR}"/attempt-* "${OTA_DIR}"/.partial-*; do
    [ -f "${stale}" ] || continue
    [ "${stale}" != "${OTA_DIR}/release-${release}.squashfs" ] && [ "${stale}" != "${attempt_file}" ] || continue
    rm -f -- "${stale}" || return 1
  done
  attempt=$(ota_attempt_id "${attempt_file}") || return 1
  if ota_request download --dir "${OTA_DIR}" --release-id "${release}" --url "${url}" --sha256 "${digest}"; then
    OTA_IMAGE="${OTA_DIR}/release-${OTA_RELEASE}.squashfs"
  else
    rc=$?
    ota_event "${release}" "${attempt}" failed download || log "Could not queue download failure"
    rm -f -- "${attempt_file}"
    return "${rc}"
  fi

  ota_event "${OTA_RELEASE}" "${attempt}" downloaded || log "Could not queue download telemetry"
  progress 100 "Signed download verified"
}

cmd_ota_download() (
  ota_load_config || die "Cannot load OTA configuration"
  [ -n "${UPDATE_API_URL:-}" ] || die "Remote OTA is not configured"
  ota_paths
  ota_lock || die "Another OTA operation is active"
  ota_download_locked "${1:-}" "${2:-}" || return $?
  printf '%s\n' "${OTA_IMAGE}"
)

ota_apply_locked() {
  local image="$1" expected first_release first_sha first_size running target attempt attempt_file boot_id rc
  ota_reconcile || log "Could not reconcile the previous OTA attempt"
  [ ! -f "${OTA_JOURNAL}" ] || die "Finish or recover the existing OTA boot attempt first"

  ota_boot_arguments || die "Cannot verify the running RGB30 boot components"
  ota_request discover || die "This download has no eligible live signed offer"
  expected="${OTA_DIR}/release-${OTA_RELEASE}.squashfs"
  [ -f "${expected}" ] && [ ! -L "${expected}" ] && [ -f "${image}" ] && [ ! -L "${image}" ] &&
    [ "$(readlink -f -- "${image}")" = "$(readlink -f -- "${expected}")" ] ||
    die "Download the currently offered release before applying"

  first_release="${OTA_RELEASE}" first_sha="${OTA_SHA}" first_size="${OTA_SIZE}"
  attempt_file="${OTA_DIR}/attempt-${OTA_RELEASE}"
  attempt=$(ota_attempt_id "${attempt_file}") || return 1
  if [ "$(stat -c %s "${image}")" != "${OTA_SIZE}" ] ||
    [ "$(sha256sum "${image}" | awk '{print $1}')" != "${OTA_SHA}" ] ||
    [ "$(unsquashfs -cat "${image}" etc/gaoos-version)" != "${OTA_VERSION}" ] ||
    [ "$(unsquashfs -cat "${image}" etc/gaoos-ota-sequence)" != "${OTA_SEQUENCE}" ]; then
    ota_event "${OTA_RELEASE}" "${attempt}" failed validation || log "Could not queue validation failure"
    rm -f -- "${attempt_file}"
    die "Downloaded root filesystem differs from the signed release"
  fi

  # Renew the offer after local validation and immediately before the write
  # transaction. A paused, expired, revoked or replaced offer cannot stage.
  ota_boot_arguments || die "Cannot revalidate the running RGB30 boot components"
  ota_request discover || die "This release is no longer offered"
  [ "${OTA_RELEASE}" = "${first_release}" ] && [ "${OTA_SHA}" = "${first_sha}" ] &&
    [ "${OTA_SIZE}" = "${first_size}" ] || die "This release has been replaced"

  running=$(get_running_slot) || return 1
  target=$(get_inactive_slot "${running}") || return 1
  boot_id=$(ota_boot_id) || return 1
  ota_write_journal "${OTA_RELEASE}" "${attempt}" "${running}" "${target}" \
    "${OTA_VERSION}" "${OTA_SEQUENCE}" "${boot_id}" intent || die "Cannot record the OTA write intent"

  if cmd_apply "${image}" "${OTA_SHA}" "${OTA_SIZE}"; then
    ota_write_journal "${OTA_RELEASE}" "${attempt}" "${running}" "${target}" \
      "${OTA_VERSION}" "${OTA_SEQUENCE}" "${boot_id}" staged || log "Could not update the OTA journal"
    ota_event "${OTA_RELEASE}" "${attempt}" staged || log "Could not queue staging telemetry"
    rm -f -- "${image}" "${attempt_file}" || log "Could not remove the applied download"
  else
    rc=$?
    # A failed flush after renaming boot state can leave a pending slot. Keep
    # its journal for the next boot to resolve.
    if [ "$(ota_state_value active_slot)" = "${running}" ]; then
      ota_event "${OTA_RELEASE}" "${attempt}" failed write || log "Could not queue failure telemetry"
      rm -f -- "${OTA_JOURNAL}" "${attempt_file}"
    fi
    return "${rc}"
  fi
}

cmd_ota_apply() (
  local image="${1:-}" resolved reserved
  [ -f "${image}" ] || die "Usage: gaoos-update apply <existing image>"
  resolved=$(readlink -f -- "${image}") || die "Cannot resolve update image"
  reserved=$(readlink -f -- "${STAGING_DIR}/.ota") || reserved=""
  if [[ "${image}" == "${STAGING_DIR}/.ota/"* ||
    ( -n "${reserved}" && "${resolved}" == "${reserved}/"* ) ]]; then
    [[ -n "${reserved}" && "${resolved}" == "${reserved}/"* && ! -L "${image}" ]] ||
      die "Invalid OTA staging path"
    ota_load_config || die "Cannot load OTA configuration"
    [ -n "${UPDATE_API_URL:-}" ] || die "Remote OTA is not configured"
    ota_paths
    ota_lock || die "Another OTA operation is active"
    ota_apply_locked "${resolved}"
  else
    cmd_apply "${image}"
  fi
)

cmd_ota_canary() (
  ota_load_config || die "Cannot load OTA configuration"
  [ -n "${UPDATE_API_URL:-}" ] || { log "Remote OTA is not configured"; return 0; }
  ota_paths
  ota_lock || die "Another OTA operation is active"
  local rc
  if ota_download_locked; then
    ota_apply_locked "${OTA_IMAGE}"
  else
    rc=$?
    [ "${rc}" -eq 3 ] || return "${rc}"
  fi
)
