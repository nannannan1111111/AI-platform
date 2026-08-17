#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly script_dir
deployment_root="$(cd -- "$script_dir/.." && pwd)"
readonly deployment_root
readonly compose_file="${COMPOSE_FILE:-/opt/infinite-canvas/compose.production.yml}"
readonly environment_file="${DEPLOY_ENV_FILE:-/etc/infinite-canvas/production.env}"
readonly caddy_environment_file="${CADDY_ENV_FILE:-/etc/infinite-canvas/caddy.env}"
readonly caddy_config_file="${CADDY_CONFIG_FILE:-/etc/caddy/Caddyfile}"
readonly release_directory="${RELEASE_EVIDENCE_DIR:-/srv/infinite-canvas/releases}"

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "required command is missing: $1" >&2
    exit 1
  }
}

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || {
    echo "required environment variable is missing: $name" >&2
    exit 1
  }
}

read_environment_value() {
  local name="$1"
  sed -n "s/^${name}=//p" "$environment_file" | tail -n 1
}

read_caddy_environment_value() {
  local name="$1"
  sed -n "s/^${name}=//p" "$caddy_environment_file" | tail -n 1
}

if [[ "$(id -u)" -ne 0 ]]; then
  echo "deploy-release.sh must run as root" >&2
  exit 1
fi

for command_name in caddy cosign curl docker findmnt install sed sha256sum systemctl; do
  require_command "$command_name"
done

require_value SITE_DOMAIN
require_value COSIGN_CERTIFICATE_IDENTITY_REGEXP
require_value COSIGN_CERTIFICATE_OIDC_ISSUER

[[ -f "$environment_file" ]] || {
  echo "deployment environment file does not exist: $environment_file" >&2
  exit 1
}
[[ -f "$compose_file" ]] || {
  echo "production Compose file does not exist: $compose_file" >&2
  exit 1
}
[[ -f "$caddy_environment_file" ]] || {
  echo "Caddy environment file does not exist: $caddy_environment_file" >&2
  exit 1
}

environment_mode="$(stat -c '%a' "$environment_file")"
if (( 8#$environment_mode & 077 )); then
  echo "deployment environment file must not be readable or writable by group/other (current mode $environment_mode)" >&2
  exit 1
fi
caddy_environment_mode="$(stat -c '%a' "$caddy_environment_file")"
if (( 8#$caddy_environment_mode & 077 )); then
  echo "Caddy environment file must not be readable or writable by group/other (current mode $caddy_environment_mode)" >&2
  exit 1
fi
if [[ "$(read_caddy_environment_value SITE_DOMAIN)" != "$SITE_DOMAIN" ]]; then
  echo "Caddy SITE_DOMAIN must exactly match the deployment SITE_DOMAIN" >&2
  exit 1
fi

image_reference="$(read_environment_value CREATIVE_STUDIO_IMAGE)"
if [[ ! "$image_reference" =~ ^[^[:space:]]+@sha256:[0-9a-f]{64}$ ]]; then
  echo "CREATIVE_STUDIO_IMAGE must be an immutable sha256 digest reference" >&2
  exit 1
fi
if [[ "$image_reference" == *":latest"* ]]; then
  echo "latest is forbidden for production deployments" >&2
  exit 1
fi

allowed_hosts="$(read_environment_value ALLOWED_HOSTS)"
enable_hsts="$(read_environment_value ENABLE_HSTS)"
trusted_proxies="$(read_environment_value TRUSTED_PROXY_CIDRS)"
database_url="$(read_environment_value DATABASE_URL)"

[[ ",$allowed_hosts," == *",$SITE_DOMAIN,"* ]] || {
  echo "ALLOWED_HOSTS must contain SITE_DOMAIN exactly" >&2
  exit 1
}
[[ "$enable_hsts" == "true" ]] || {
  echo "ENABLE_HSTS must be true for the verified HTTPS deployment" >&2
  exit 1
}
[[ "$trusted_proxies" == "127.0.0.1/32" ]] || {
  echo "TRUSTED_PROXY_CIDRS must include only the direct Caddy loopback boundary (127.0.0.1/32)" >&2
  exit 1
}
[[ "$database_url" == postgresql+psycopg://* ]] || {
  echo "DATABASE_URL must use the postgresql+psycopg driver" >&2
  exit 1
}
[[ "$database_url" == *"sslmode="* ]] || {
  echo "DATABASE_URL must explicitly require or verify PostgreSQL TLS using sslmode" >&2
  exit 1
}

findmnt --mountpoint /srv/infinite-canvas >/dev/null || {
  echo "/srv/infinite-canvas is not a mounted data disk" >&2
  exit 1
}
[[ "$(stat -c '%u:%g:%a' /srv/infinite-canvas/data/generated-media)" == "10001:10001:750" ]] || {
  echo "generated media directory ownership/mode must be 10001:10001:750" >&2
  exit 1
}
[[ "$(stat -c '%u:%g:%a' /srv/infinite-canvas/secrets/providers)" == "10001:10001:700" ]] || {
  echo "provider secrets directory ownership/mode must be 10001:10001:700" >&2
  exit 1
}

release_digest="${image_reference##*@sha256:}"
release_path="$release_directory/$release_digest"
install -d -o root -g root -m 0700 "$release_path"

echo "verifying the keyless signature for $image_reference"
cosign verify \
  --certificate-identity-regexp "$COSIGN_CERTIFICATE_IDENTITY_REGEXP" \
  --certificate-oidc-issuer "$COSIGN_CERTIFICATE_OIDC_ISSUER" \
  --output json \
  "$image_reference" > "$release_path/cosign-verification.json"
chmod 0600 "$release_path/cosign-verification.json"

docker compose --env-file "$environment_file" -f "$compose_file" config --quiet
docker compose --env-file "$environment_file" -f "$compose_file" pull

echo "running database migrations before starting application traffic"
docker compose --env-file "$environment_file" -f "$compose_file" run --rm --no-deps migrate

worker_replicas="$(read_environment_value GENERATION_WORKER_REPLICAS)"
worker_replicas="${worker_replicas:-4}"
[[ "$worker_replicas" =~ ^[1-9][0-9]*$ ]] || {
  echo "GENERATION_WORKER_REPLICAS must be a positive integer" >&2
  exit 1
}

docker compose --env-file "$environment_file" -f "$compose_file" up -d --no-deps creative-studio
docker compose --env-file "$environment_file" -f "$compose_file" up -d --no-deps --scale "generation-worker=$worker_replicas" generation-worker

creative_studio_port="$(read_environment_value CREATIVE_STUDIO_PORT)"
creative_studio_port="${creative_studio_port:-8000}"
[[ "$creative_studio_port" =~ ^[0-9]+$ ]] || {
  echo "CREATIVE_STUDIO_PORT must be numeric" >&2
  exit 1
}

ready=false
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 5 "http://127.0.0.1:$creative_studio_port/readyz" >/dev/null; then
    ready=true
    break
  fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo "application readiness failed; Caddy was not changed or enabled" >&2
  exit 1
fi

caddy validate --config "$deployment_root/Caddyfile" --adapter caddyfile
install -o root -g root -m 0644 "$deployment_root/Caddyfile" "$caddy_config_file"
install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/infinite-canvas.conf <<EOF
[Service]
EnvironmentFile=$caddy_environment_file
EOF
systemctl daemon-reload

caddy_was_active=false
if systemctl is-active --quiet caddy; then
  caddy_was_active=true
  systemctl reload caddy
else
  systemctl enable --now caddy
fi

https_ready=false
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 10 "https://$SITE_DOMAIN/readyz" >/dev/null; then
    https_ready=true
    break
  fi
  sleep 2
done
if [[ "$https_ready" != true ]]; then
  if [[ "$caddy_was_active" != true ]]; then
    systemctl disable --now caddy || true
  fi
  echo "public HTTPS readiness failed after Caddy activation" >&2
  exit 1
fi

{
  printf 'deployed_at=%s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
  printf 'image=%s\n' "$image_reference"
  printf 'site=https://%s\n' "$SITE_DOMAIN"
} > "$release_path/deployment.txt"
chmod 0600 "$release_path/deployment.txt"

echo "deployment passed migrations, loopback readiness, signature verification, and public HTTPS readiness"
