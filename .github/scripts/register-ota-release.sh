#!/usr/bin/env bash
# Upload a signed, content-addressed payload and register the signed envelope.
set -euo pipefail

qualification=0
if [[ "${1:-}" == '--allow-qualification' ]]; then
  qualification=1
  shift
fi
if [[ $# != 3 ]]; then
  echo "Usage: register-ota-release.sh [--allow-qualification] ARTIFACT VALIDATION_REPORT SIGNED_ENVELOPE" >&2
  exit 2
fi
artifact=$1
report=$2
envelope=$3

: "${GAOOS_OTA_S3_ENDPOINT:?Set the HTTPS S3 API endpoint}"
: "${GAOOS_OTA_S3_BUCKET:?Set the S3 bucket}"
: "${GAOOS_OTA_API_URL:?Set the OTA control-plane origin}"
: "${GAOOS_OTA_PUBLICATION_TOKEN:?Set the OTA registration token}"
for url in "$GAOOS_OTA_S3_ENDPOINT" "$GAOOS_OTA_API_URL"; do
  if ! [[ "$url" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/?$ ]]; then
    echo "OTA and S3 endpoints must be HTTPS origins" >&2
    exit 2
  fi
done
if ! [[ "$GAOOS_OTA_PUBLICATION_TOKEN" =~ ^[[:graph:]]+$ ]]; then
  echo "Invalid OTA registration credential format" >&2
  exit 2
fi

read -r sha256 bytes checksum_sha256 < <(
  python3 - "$artifact" "$report" "$envelope" "$qualification" <<'PY'
import base64
import hashlib
import json
from pathlib import Path
import sys

artifact, report_file, envelope_file = map(Path, sys.argv[1:4])
report = json.loads(report_file.read_bytes())
encoded = json.loads(envelope_file.read_bytes())['manifest']
manifest = json.loads(base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)))
size = artifact.stat().st_size
with artifact.open('rb') as f:
    sha256 = hashlib.sha256()
    while chunk := f.read(4 * 1024 * 1024):
        sha256.update(chunk)
digest = sha256.digest()
if sys.argv[4] == '1':
    verified = (report.get('status') == 'QUALIFICATION_ARTIFACT_VALIDATED' and
                report.get('classification') == 'TEST_ONLY' and manifest.get('channel') == 'nightly')
else:
    verified = report.get('status') == 'PASS'
if (not verified or
        any(report['ota'][field] != manifest[field] for field in report['ota']) or
        manifest['size_bytes'] != size or manifest['sha256'] != digest.hex()):
    sys.exit('Envelope or artifact differs from validation')
print(digest.hex(), size, base64.b64encode(digest).decode('ascii'))
PY
)

# AWS CLI's path-style addressing supports S3-compatible endpoints backed by a
# single certificate. Credentials stay in the environment.
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export AWS_CONFIG_FILE="$work/aws-config" AWS_PAGER=""
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
cat > "$AWS_CONFIG_FILE" <<'AWS_CONFIG'
[default]
s3 =
    addressing_style = path
AWS_CONFIG

object_key="sha256/$sha256.squashfs"
s3=(aws --endpoint-url "$GAOOS_OTA_S3_ENDPOINT" s3api)
if ! "${s3[@]}" put-object --bucket "$GAOOS_OTA_S3_BUCKET" --key "$object_key" \
  --body "$artifact" --content-type application/octet-stream --cache-control 'public, max-age=31536000, immutable' \
  --checksum-algorithm SHA256 --checksum-sha256 "$checksum_sha256" --if-none-match '*' > "$work/put.json" 2> "$work/put.err"; then
  # A retry may find an already-created object. HEAD below must still prove
  # exact length and a storage-verified full-object checksum.
  echo "Conditional upload did not complete. Checking the existing object." >&2
fi
"${s3[@]}" head-object --bucket "$GAOOS_OTA_S3_BUCKET" --key "$object_key" \
  --checksum-mode ENABLED --output json > "$work/head.json"
jq -e --arg sha "$checksum_sha256" --argjson size "$bytes" \
  '.ChecksumSHA256 == $sha and .ContentLength == $size and (.ChecksumType // "FULL_OBJECT") == "FULL_OBJECT"' \
  "$work/head.json" >/dev/null || {
  echo "Stored artifact checksum or length differs from validation" >&2
  exit 1
}

printf 'Authorization: Bearer %s\n' "$GAOOS_OTA_PUBLICATION_TOKEN" |
  curl --silent --show-error --fail --proto '=https' --connect-timeout 10 \
    --max-time 90 --retry 4 --retry-connrefused --output /dev/null \
    --header @- --json "@$envelope" \
    "${GAOOS_OTA_API_URL%/}/api/v1/operator/releases"
