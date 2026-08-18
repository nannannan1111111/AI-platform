from sqlalchemy import create_engine
from sqlalchemy.pool import QueuePool

from app.database_metrics import install_database_pool_metrics
from app.observability import MetricsRegistry


def test_database_pool_metrics_track_connection_lifecycle() -> None:
    engine = create_engine("sqlite:///:memory:", poolclass=QueuePool, pool_size=1, max_overflow=0)
    metrics = MetricsRegistry()
    install_database_pool_metrics(engine, metrics)
    install_database_pool_metrics(engine, metrics)

    with engine.connect():
        rendered = metrics.render()
        assert "database_pool_checkouts_total" in rendered
        assert "database_pool_checked_out" in rendered
        assert "database_pool_size" in rendered

    rendered = metrics.render()
    assert 'database_pool_checked_out 0' in rendered
    assert "database_pool_checkins_total" in rendered
    engine.dispose()
