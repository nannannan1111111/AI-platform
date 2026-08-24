#!/usr/bin/env bash
set -Eeuo pipefail

readonly compose_file="${COMPOSE_FILE:-/opt/infinite-canvas/compose.production.yml}"
readonly environment_file="${DEPLOY_ENV_FILE:-/etc/infinite-canvas/single-host.env}"
readonly evidence_root="${RELEASE_EVIDENCE_DIR:-/root/data/disk/infinite-canvas/releases}"
readonly candidate_image="${CANDIDATE_IMAGE:?set CANDIDATE_IMAGE to the prepared V27 image tag or digest}"
readonly rollback_image="${ROLLBACK_IMAGE:?set ROLLBACK_IMAGE to the retained V26 image tag or digest}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "deploy-single-host-candidate.sh must run as root" >&2
  exit 1
fi

for command_name in curl docker install sed sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "required command is missing: $command_name" >&2
    exit 1
  }
done

[[ -f "$compose_file" ]] || { echo "missing Compose file: $compose_file" >&2; exit 1; }
[[ -f "$environment_file" ]] || { echo "missing environment file: $environment_file" >&2; exit 1; }
[[ "$(stat -c '%a' "$environment_file")" == "600" ]] || {
  echo "environment file must have mode 600" >&2
  exit 1
}

docker image inspect "$candidate_image" >/dev/null
docker image inspect "$rollback_image" >/dev/null
candidate_id="$(docker image inspect "$candidate_image" --format '{{.Id}}')"
rollback_id="$(docker image inspect "$rollback_image" --format '{{.Id}}')"
[[ "$candidate_id" != "$rollback_id" ]] || {
  echo "candidate and rollback images must be different" >&2
  exit 1
}

candidate_head="$(docker run --rm --entrypoint python "$candidate_image" \
  -m alembic -c /app/backend/alembic.ini heads | tail -n 1 | awk '{print $1}')"
database_head="$(docker compose --env-file "$environment_file" -f "$compose_file" \
  exec -T creative-studio sh -lc \
  'python -m alembic -c /app/backend/alembic.ini current' | tail -n 1 | awk '{print $1}')"
[[ -n "$candidate_head" && "$candidate_head" == "$database_head" ]] || {
  echo "candidate migration head ($candidate_head) differs from database head ($database_head)" >&2
  exit 1
}

worker_replicas="$(sed -n 's/^GENERATION_WORKER_REPLICAS=//p' "$environment_file" | tail -n 1)"
worker_replicas="${worker_replicas:-10}"
[[ "$worker_replicas" =~ ^[1-9][0-9]*$ ]] || {
  echo "GENERATION_WORKER_REPLICAS must be a positive integer" >&2
  exit 1
}

release_id="$(date --utc +%Y%m%dT%H%M%SZ)-${candidate_id#sha256:}"
release_path="$evidence_root/$release_id"
install -d -o root -g root -m 0700 "$release_path"
install -o root -g root -m 0600 "$environment_file" "$release_path/single-host.env.before"
sha256sum "$compose_file" > "$release_path/compose.sha256"

set_image() {
  local image="$1"
  local temporary_file
  temporary_file="$(mktemp "${environment_file}.XXXXXX")"
  awk -v image="$image" '
    BEGIN { replaced = 0 }
    /^CREATIVE_STUDIO_IMAGE=/ { print "CREATIVE_STUDIO_IMAGE=" image; replaced = 1; next }
    { print }
    END { if (!replaced) print "CREATIVE_STUDIO_IMAGE=" image }
  ' "$environment_file" > "$temporary_file"
  chown root:root "$temporary_file"
  chmod 0600 "$temporary_file"
  mv -f "$temporary_file" "$environment_file"
}

wait_ready() {
  local ready=false
  for _ in $(seq 1 60); do
    if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/healthz >/dev/null \
      && curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/readyz >/dev/null; then
      ready=true
      break
    fi
    sleep 2
  done
  [[ "$ready" == true ]]
}

rollback() {
  trap - ERR
  echo "candidate failed; restoring $rollback_image" >&2
  set_image "$rollback_image"
  docker compose --env-file "$environment_file" -f "$compose_file" up -d --no-deps creative-studio
  docker compose --env-file "$environment_file" -f "$compose_file" \
    up -d --no-deps --scale "generation-worker=$worker_replicas" generation-worker
  wait_ready || echo "rollback readiness also failed; manual intervention required" >&2
  printf 'status=rolled-back\nrollback_image=%s\n' "$rollback_image" > "$release_path/result.txt"
  exit 1
}
trap rollback ERR

set_image "$candidate_image"
docker compose --env-file "$environment_file" -f "$compose_file" config --quiet
docker compose --env-file "$environment_file" -f "$compose_file" run --rm --no-deps migrate
docker compose --env-file "$environment_file" -f "$compose_file" up -d --no-deps creative-studio
docker compose --env-file "$environment_file" -f "$compose_file" \
  up -d --no-deps --scale "generation-worker=$worker_replicas" generation-worker
wait_ready

mapfile -t running_containers < <(
  docker compose --env-file "$environment_file" -f "$compose_file" ps -q creative-studio generation-worker
)
[[ "${#running_containers[@]}" -eq "$((worker_replicas + 1))" ]] || {
  echo "unexpected Web/Worker container count: ${#running_containers[@]}" >&2
  false
}
for container_id in "${running_containers[@]}"; do
  [[ "$(docker inspect "$container_id" --format '{{.Image}}')" == "$candidate_id" ]] || {
    echo "Web/Worker image mismatch for container $container_id" >&2
    false
  }
done

{
  printf 'status=deployed\n'
  printf 'deployed_at=%s\n' "$(date --utc +%Y-%m-%dT%H:%M:%SZ)"
  printf 'candidate_image=%s\n' "$candidate_image"
  printf 'candidate_id=%s\n' "$candidate_id"
  printf 'rollback_image=%s\n' "$rollback_image"
  printf 'rollback_id=%s\n' "$rollback_id"
  printf 'migration_head=%s\n' "$candidate_head"
  printf 'worker_replicas=%s\n' "$worker_replicas"
} > "$release_path/result.txt"
chmod 0600 "$release_path/result.txt" "$release_path/compose.sha256"
trap - ERR

echo "single-host candidate passed migration, health, readiness, and image consistency checks"
