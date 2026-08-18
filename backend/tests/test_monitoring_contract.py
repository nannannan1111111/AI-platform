from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def _read(relative_path: str) -> str:
    return (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")


def test_storage_and_backup_alerts_cover_actionable_failure_modes() -> None:
    rules = _read("deploy/monitoring/storage-backup-alerts.yml")
    runbook = _read("docs/runbooks/storage-and-backup-alerts.md")

    for alert in (
        "InfiniteCanvasMediaStorageProbeFailed",
        "InfiniteCanvasMediaStorageNearlyFull",
        "InfiniteCanvasBackupMetricsMissing",
        "InfiniteCanvasBackupRecoveryPointInvalid",
        "InfiniteCanvasBackupRecoveryPointStale",
    ):
        assert f"alert: {alert}" in rules
    assert "> 0.80" in rules
    assert "> 93600" in rules
    assert rules.count("runbook_url:") == 5
    for heading in (
        "Media storage probe failed",
        "Media storage nearly full",
        "Backup metrics missing",
        "Backup recovery point invalid",
        "Backup recovery point stale",
    ):
        assert f"## {heading}" in runbook
