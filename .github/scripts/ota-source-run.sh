#!/usr/bin/env bash
# Resolve a trusted RGB30 build run to the identity used by the signer.
set -euo pipefail

source_run=${1:?Provide a build run ID}
if [[ "${GITHUB_REPOSITORY}:${GITHUB_REF}" != 'Gao-OS/rocknix:refs/heads/gaoos' ||
      ! "${source_run}" =~ ^[1-9][0-9]*$ ]]; then
  echo 'OTA registration requires a numeric build run ID on Gao-OS/rocknix@gaoos' >&2
  exit 1
fi

run_api="repos/${GITHUB_REPOSITORY}/actions/runs"
source_run_info=$(gh api "${run_api}/${source_run}")
if ! jq -e --arg run "${source_run}" --arg current "${GITHUB_RUN_ID}" --arg sha "${GITHUB_SHA}" '
  (.id | tostring) == $run and
  .repository.full_name == "Gao-OS/rocknix" and .head_repository.full_name == "Gao-OS/rocknix" and
  .path == ".github/workflows/build-nightly.yml" and .event == "workflow_dispatch" and
  .head_branch == "gaoos" and (.head_sha | test("^[a-f0-9]{40}$")) and
  (.run_number | type == "number" and . > 0 and . == floor) and
  (if $run == $current then .head_sha == $sha else .status == "completed" and .conclusion == "success" end)
' <<< "${source_run_info}" > /dev/null; then
  echo 'Expected the current RGB30 Build run or a completed successful Build run from Gao-OS/rocknix@gaoos' >&2
  exit 1
fi

created=$(gh api "${run_api}/${GITHUB_RUN_ID}" --jq .created_at)
issued=$(date --date="${created}" +%s)
{
  printf 'run_id=%s\n' "${source_run}"
  jq -r '"commit=\(.head_sha)\nsequence=\(.run_number)"' <<< "${source_run_info}"
  printf 'issued=%s\nexpires=%s\n' "${issued}" "$((issued + 30 * 86400))"
} >> "${GITHUB_OUTPUT}"
