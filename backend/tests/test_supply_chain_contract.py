import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def _normalized_name(requirement: str) -> str:
    return re.split(r"[<>=!~\[]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def _locked_names(lock: str) -> set[str]:
    return {
        match.group(1).lower().replace("_", "-")
        for match in re.finditer(r"(?m)^([a-zA-Z0-9_.-]+)==[^\s\\]+\s*\\$", lock)
    }


def test_python_locks_pin_and_hash_every_resolved_dependency() -> None:
    production_lock = _read("backend/requirements.lock")
    development_lock = _read("backend/requirements-dev.lock")

    for lock in (production_lock, development_lock):
        requirement_starts = list(re.finditer(r"(?m)^([a-zA-Z0-9_.-]+)==[^\s\\]+\s*\\$", lock))
        assert requirement_starts
        for index, requirement in enumerate(requirement_starts):
            end = requirement_starts[index + 1].start() if index + 1 < len(requirement_starts) else len(lock)
            assert "--hash=sha256:" in lock[requirement.start() : end]

    pyproject = tomllib.loads((BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    production_names = _locked_names(production_lock)
    development_names = _locked_names(development_lock)
    declared_production = {_normalized_name(item) for item in pyproject["project"]["dependencies"]}
    declared_development = {_normalized_name(item) for item in pyproject["project"]["optional-dependencies"]["dev"]}

    assert declared_production <= production_names
    assert declared_production | declared_development <= development_names
    assert declared_development.isdisjoint(production_names)


def test_production_image_uses_pinned_multistage_inputs_and_locked_dependencies() -> None:
    dockerfile = _read("Dockerfile")

    assert "python:3.12-alpine@sha256:" in dockerfile
    assert "node:24.19.0-alpine@sha256:" in dockerfile
    assert "AS frontend-builder" in dockerfile
    assert "AS python-dependencies" in dockerfile
    assert "AS runtime" in dockerfile
    assert "COPY backend/requirements.lock" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--only-binary=:all:" in dockerfile
    assert "--no-compile" in dockerfile
    assert "npm ci" in dockerfile
    assert "npm run build" in dockerfile
    assert "COPY --from=frontend-builder" in dockerfile
    assert "org.opencontainers.image.revision" in dockerfile
    assert "USER app" in dockerfile
    assert "pip install --no-cache-dir --disable-pip-version-check ./backend" not in dockerfile


def test_supply_chain_workflow_generates_evidence_blocks_vulnerabilities_and_signs_tags() -> None:
    workflow = _read(".github/workflows/supply-chain.yml")

    assert "name: image-sbom-vulnerability-gate" in workflow
    assert "./scripts/update-python-locks.ps1 -Check" in workflow
    assert "run: mkdir -p artifacts" in workflow
    assert "format: spdx-json" in workflow
    assert "ghcr.io/aquasecurity/trivy:0.65.0@sha256:" in workflow
    assert "aquasecurity/trivy-action@" not in workflow
    assert "--severity CRITICAL,HIGH" in workflow
    assert "--exit-code 1" in workflow
    assert "--ignore-unfixed=false" in workflow
    assert "--ignorefile /workspace/.trivyignore.yaml" in workflow
    assert "name: publish-signed-image" in workflow
    assert "startsWith(github.ref, 'refs/tags/v')" in workflow
    assert "id-token: write" in workflow
    assert "sbom: true" in workflow
    assert "provenance: mode=max" in workflow
    assert "cosign sign --yes" in workflow
    assert "name: supply-chain-gate" in workflow

    action_references = re.findall(r"uses:\s+[^@\s]+@([^\s]+)", workflow + _read(".github/workflows/quality-gate.yml"))
    assert action_references
    assert all(re.fullmatch(r"[0-9a-f]{40}", reference) for reference in action_references)


def test_dependency_updates_and_expiring_vulnerability_exceptions_are_configured() -> None:
    dependabot = _read(".github/dependabot.yml")
    exceptions = _read(".trivyignore.yaml")

    for ecosystem in ("pip", "npm", "docker", "github-actions"):
        assert f"package-ecosystem: {ecosystem}" in dependabot
    assert "vulnerabilities: []" in exceptions
    assert "expiry date" in exceptions
