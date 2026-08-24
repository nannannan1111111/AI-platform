from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
TENCENT_ROOT = REPOSITORY_ROOT / "deploy" / "tencent-cloud"
INFRASTRUCTURE_ROOT = TENCENT_ROOT / "infra"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_tencent_cloud_provider_and_remote_state_are_pinned() -> None:
    versions = _read(INFRASTRUCTURE_ROOT / "versions.tf")
    provider_lock = _read(INFRASTRUCTURE_ROOT / ".terraform.lock.hcl")
    backend_example = _read(INFRASTRUCTURE_ROOT / "backend.hcl.example")

    assert 'version = "= 1.83.23"' in versions
    assert 'backend "s3"' in versions
    assert 'version     = "1.83.23"' in provider_lock
    assert "use_lockfile" in backend_example
    assert "encrypt" in backend_example
    assert "\naccess_key" not in backend_example
    assert "\nsecret_key" not in backend_example


def test_public_network_exposes_only_edge_ports_and_restricted_ssh() -> None:
    infrastructure = _read(INFRASTRUCTURE_ROOT / "main.tf")
    variables = _read(INFRASTRUCTURE_ROOT / "variables.tf")

    assert 'port        = "80"' in infrastructure
    assert 'port        = "443"' in infrastructure
    assert 'port        = "22"' in infrastructure
    assert "cidr_block  = var.operator_cidr" in infrastructure
    assert 'port        = "8000"' not in infrastructure
    assert "source_security_id = tencentcloud_security_group.application.id" in infrastructure
    assert 'port               = "5432"' in infrastructure
    database_rules = infrastructure.split(
        'resource "tencentcloud_security_group_rule_set" "database"', maxsplit=1
    )[1].split('resource "tencentcloud_cbs_storage"', maxsplit=1)[0]
    assert 'cidr_block  = "0.0.0.0/0"' not in database_rules
    assert '"operator_cidr must be a valid restricted IPv4 /24 through /32 CIDR."' in variables


def test_managed_postgresql_is_private_tls_and_version_17() -> None:
    infrastructure = _read(INFRASTRUCTURE_ROOT / "main.tf")
    variables = _read(INFRASTRUCTURE_ROOT / "variables.tf")
    outputs = _read(INFRASTRUCTURE_ROOT / "outputs.tf")

    assert 'default     = "17"' in variables
    assert 'var.postgresql_major_version == "17"' in variables
    assert 'resource "tencentcloud_postgresql_instance" "production"' in infrastructure
    assert 'data "tencentcloud_postgresql_db_versions" "required"' in infrastructure
    assert "do not silently downgrade" in infrastructure
    assert "delete_protection" in infrastructure
    assert 'resource "tencentcloud_postgresql_instance_ssl_config" "production"' in infrastructure
    assert "ssl_enabled     = true" in infrastructure
    assert "postgresql_root_password" not in outputs


def test_media_disk_and_registry_are_private_and_hardened() -> None:
    infrastructure = _read(INFRASTRUCTURE_ROOT / "main.tf")
    cloud_init = _read(TENCENT_ROOT / "cloud-init.yaml.tftpl")

    assert 'resource "tencentcloud_cbs_storage" "media"' in infrastructure
    assert "encrypt           = true" in infrastructure
    assert "open_public_operation = false" in infrastructure
    assert "is_public      = false" in infrastructure
    assert "is_auto_scan   = true" in infrastructure
    assert "is_prevent_vul = true" in infrastructure
    assert "PROVIDER_SECRETS" not in cloud_init
    assert 'install -d -o 10001 -g 10001 -m 0750 "$mount_path/data/generated-media"' in cloud_init
    assert 'install -d -o 10001 -g 10001 -m 0700 "$mount_path/secrets/providers"' in cloud_init
    assert "systemctl, disable, --now, caddy" in cloud_init


def test_caddy_streaming_and_tls_boundary_is_explicit() -> None:
    caddyfile = _read(TENCENT_ROOT / "Caddyfile")

    assert "reverse_proxy 127.0.0.1:" in caddyfile
    assert "flush_interval -1" in caddyfile
    assert "response_header_timeout {$UPSTREAM_RESPONSE_TIMEOUT:11m}" in caddyfile
    assert "max_size {$MAX_REQUEST_BODY_SIZE:32MB}" in caddyfile
    assert "health_uri /readyz" in caddyfile


def test_release_script_opens_https_only_after_all_hard_gates() -> None:
    deploy_script = _read(TENCENT_ROOT / "scripts" / "deploy-release.sh")
    signature_index = deploy_script.index("cosign verify")
    migration_index = deploy_script.index("run --rm --no-deps migrate")
    readiness_index = deploy_script.index('"http://127.0.0.1:$creative_studio_port/readyz"')
    caddy_index = deploy_script.index("systemctl enable --now caddy")

    assert signature_index < migration_index < readiness_index < caddy_index
    assert "CREATIVE_STUDIO_IMAGE must be an immutable sha256 digest reference" in deploy_script
    assert "ENABLE_HSTS must be true" in deploy_script
    assert "DATABASE_URL must explicitly require or verify PostgreSQL TLS" in deploy_script
    assert "application readiness failed; Caddy was not changed or enabled" in deploy_script
    assert "systemctl disable --now caddy || true" in deploy_script
    assert 'DEPLOY_ENV_FILE:-/etc/infinite-canvas/single-host.env' in deploy_script


def test_tencent_production_environment_uses_loopback_proxy_and_hsts() -> None:
    environment_example = _read(TENCENT_ROOT / "production.env.example")

    assert "TRUSTED_PROXY_CIDRS=127.0.0.1/32" in environment_example
    assert "ENABLE_HSTS=true" in environment_example
    assert "sslmode=require" in environment_example
    assert "GENERATED_MEDIA_HOST_PATH=/srv/infinite-canvas/data/generated-media" in environment_example
    assert "PROVIDER_SECRETS_HOST_PATH=/srv/infinite-canvas/secrets/providers" in environment_example


def test_single_host_candidate_release_has_automatic_v26_rollback() -> None:
    deploy_script = _read(TENCENT_ROOT / "scripts" / "deploy-single-host-candidate.sh")

    assert "/etc/infinite-canvas/single-host.env" in deploy_script
    assert "/root/data/disk/infinite-canvas/releases" in deploy_script
    assert 'candidate migration head ($candidate_head) differs from database head ($database_head)' in deploy_script
    assert 'trap rollback ERR' in deploy_script
    assert 'set_image "$rollback_image"' in deploy_script
    assert 'run --rm --no-deps migrate' in deploy_script
    assert 'http://127.0.0.1:8000/healthz' in deploy_script
    assert 'http://127.0.0.1:8000/readyz' in deploy_script
    assert 'Web/Worker image mismatch' in deploy_script
