#!/usr/bin/env bash
set -Eeuo pipefail

source_image="${1:?usage: mirror-signed-image.sh <signed-source@sha256:digest> <private-tcr-repository:tag>}"
destination_tag="${2:?usage: mirror-signed-image.sh <signed-source@sha256:digest> <private-tcr-repository:tag>}"

if [[ ! "$source_image" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "source image must be an immutable sha256 digest reference" >&2
  exit 1
fi
if [[ "$destination_tag" == *"@"* || "$destination_tag" == *":latest" ]]; then
  echo "destination must be a non-latest version tag in the private TCR repository" >&2
  exit 1
fi

: "${COSIGN_CERTIFICATE_IDENTITY_REGEXP:?set the trusted GitHub Actions identity regexp}"
: "${COSIGN_CERTIFICATE_OIDC_ISSUER:?set the trusted GitHub OIDC issuer}"

for command_name in cosign docker; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "required command is missing: $command_name" >&2
    exit 1
  }
done

cosign verify \
  --certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP" \
  --certificate-oidc-issuer "$COSIGN_CERTIFICATE_OIDC_ISSUER" \
  "$source_image" >/dev/null

# The deployment host reaches TCR through its VPC attachment. Registry credentials
# must already be present in Docker's credential store and are never command arguments.
cosign copy "$source_image" "$destination_tag"

destination_digest="$(docker buildx imagetools inspect "$destination_tag" --format '{{.Manifest.Digest}}')"
if [[ ! "$destination_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "unable to resolve the promoted TCR digest" >&2
  exit 1
fi

destination_image="${destination_tag%:*}@$destination_digest"
cosign verify \
  --certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP" \
  --certificate-oidc-issuer "$COSIGN_CERTIFICATE_OIDC_ISSUER" \
  "$destination_image" >/dev/null

printf '%s\n' "$destination_image"
