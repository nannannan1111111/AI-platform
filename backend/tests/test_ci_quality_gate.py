from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_github_workflow_has_fixed_parallel_jobs_and_a_final_gate() -> None:
    workflow = _read(".github/workflows/quality-gate.yml")

    for job_name in ("backend-quality", "backend-tests", "frontend-quality", "production-contract"):
        assert f"name: {job_name}" in workflow
    assert "name: release-gate" in workflow
    assert "if: ${{ always() }}" in workflow
    assert "needs:" in workflow
    assert "pull_request:" in workflow
    assert "push:" in workflow
    assert "permissions:\n  contents: read" in workflow


def test_backend_ci_uses_postgresql_17_without_optional_database_tests() -> None:
    workflow = _read(".github/workflows/quality-gate.yml")
    script = _read("scripts/quality-gate.ps1")

    assert "image: postgres:17" in workflow
    assert "POSTGRES_TEST_DATABASE_URL:" in workflow
    assert "pg_isready" in workflow
    assert 'Scope backend-tests' in workflow
    assert "POSTGRES_TEST_DATABASE_URL is required" in script
    assert "python -m pytest" not in workflow


def test_frontend_ci_reinstalls_checks_builds_and_detects_artifact_drift() -> None:
    workflow = _read(".github/workflows/quality-gate.yml")
    script = _read("scripts/quality-gate.ps1")

    assert "node-version: \"24.19.0\"" in workflow
    assert "Invoke-Checked npm ci" in script
    assert "Invoke-Checked npm run check" in script
    assert "Invoke-Checked npm run build" in script
    assert 'Invoke-Checked -Command git -Arguments @("-C", $RepositoryRoot, "diff", "--exit-code", "--", "backend/app/webui/static/admin-vue")' in script


def test_runtime_data_ignores_do_not_hide_source_packages() -> None:
    ignore = _read(".gitignore")

    for directory in ("data", "media", "storage", "generated-media", "provider-secrets"):
        assert f"/{directory}/" in ignore
        assert f"\n{directory}/" not in ignore
    assert (REPOSITORY_ROOT / "backend/app/media/__init__.py").is_file()


def test_production_gate_requires_one_migration_head_compose_and_image_build() -> None:
    workflow = _read(".github/workflows/quality-gate.yml")
    script = _read("scripts/quality-gate.ps1")

    assert "Expected exactly one Alembic head" in script
    assert "docker compose -f deploy/compose.production.yml config --quiet" in script
    assert "prom/prometheus:v3.5.0@sha256:" in script
    assert "check rules /rules/storage-backup-alerts.yml" in script
    assert "docker build --tag creative-studio:quality-gate ." in script
    assert '"-chdir=deploy/tencent-cloud/infra", "validate"' in script
    assert "OpenTofu is required in CI" in script
    assert "opentofu/setup-opentofu@" in workflow
    assert 'tofu_version: "1.12.5"' in workflow
    assert "Scope production" in workflow


def test_local_quality_script_exposes_every_documented_scope() -> None:
    script = _read("scripts/quality-gate.ps1")

    assert '[ValidateSet("backend-quality", "backend-tests", "frontend", "production", "all")]' in script
