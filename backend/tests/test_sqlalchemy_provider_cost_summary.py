from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.provider_costs import ProviderCostSummary, SqlAlchemyProviderCostSummaries


def test_submitted_attempt_costs_are_grouped_by_provider_and_logical_model(tmp_path: Path) -> None:
    backend_root = Path(__file__).parents[1]
    database_url = f"sqlite+pysqlite:///{(tmp_path / 'provider-cost-summary.db').as_posix()}"
    config = Config(backend_root / "alembic.ini")
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    now = datetime(2026, 8, 11, 14, 0, tzinfo=UTC)
    later = now + timedelta(seconds=1)
    sessions = sessionmaker(create_engine(database_url), expire_on_commit=False)
    with sessions.begin() as database:
        database.execute(
            text(
                """INSERT INTO api_providers
                (id, code, display_name, protocol, base_url, secret_ref, key_fingerprint,
                 enabled, created_at, updated_at, deleted_at)
                VALUES ('provider-1', 'source-a', '来源 A', 'openai_compatible_images',
                        'https://source-a.example.com/v1', 'secret:test', 'fingerprint',
                        1, :now, :now, NULL)"""
            ),
            {"now": now},
        )
        database.execute(
            text(
                """INSERT INTO image_model_routes
                (id, provider_id, logical_model, output_spec, provider_model_name,
                 compatibility_group, priority, enabled, health_status,
                 created_at, updated_at, deleted_at)
                VALUES ('route-1', 'provider-1', 'gpt-image-2', '4k', 'upstream-image',
                        'gpt-image-2/4k/v1', 100, 1, 'healthy', :now, :now, NULL)"""
            ),
            {"now": now},
        )
        database.execute(
            text(
                """INSERT INTO provider_cost_rates
                (id, image_model_route_id, variant_code, version, provider_currency,
                 cost_per_image_micros, effective_from, published_at)
                VALUES ('cost-1', 'route-1', '', 1, 'USD', 120000, :now, :now),
                       ('cost-2', 'route-1', '', 2, 'USD', 150000, :later, :later)"""
            ),
            {"now": now, "later": later},
        )
        database.execute(
            text(
                """INSERT INTO generation_tasks
                (id, user_id, account_space_id, canvas_id, logical_model, output_spec,
                 quantity, prompt, aspect_ratio, quality, size, resolution_tier,
                 output_format, reference_media_ids, credit_freeze_id,
                 model_price_version_id, frozen_units, status, selected_route_id,
                 route_selection_reason, provider_task_id, delivered_quantity, error,
                 outcome_reference, created_at, updated_at)
                VALUES ('task-1', 'user-1', 'space-1', NULL, 'gpt-image-2', '4k',
                        2, 'prompt', '1:1', 'high', '2048x2048', '4k', 'png', '[]',
                        'freeze-1', 'price-1', 2, 'running', 'route-1', 'automatic',
                        '', NULL, '', '', :now, :now)"""
            ),
            {"now": now},
        )
        database.execute(
            text(
                """INSERT INTO image_generation_attempts
                (id, generation_task_id, attempt_no, route_id, provider_cost_rate_id,
                 provider_idempotency_key, status, provider_task_id, error_code, error,
                 submitted_at, accepted_at, finished_at, created_at, updated_at)
                VALUES ('attempt-1', 'task-1', 1, 'route-1', 'cost-1', 'key-1',
                        'failed', '', 'upstream', 'failed after submit', :now, NULL, :now, :now, :now),
                       ('attempt-2', 'task-1', 2, 'route-1', 'cost-2', 'key-2',
                        'provider_pending', 'provider-task', '', '', :now, :now, NULL, :now, :now),
                       ('attempt-3', 'task-1', 3, 'route-1', 'cost-2', 'key-3',
                        'created', '', '', '', NULL, NULL, NULL, :now, :now)"""
            ),
            {"now": now},
        )

    summaries = SqlAlchemyProviderCostSummaries(sessions).summarize()

    assert summaries == (
        ProviderCostSummary(
            provider_id="provider-1",
            provider_display_name="来源 A",
            logical_model="gpt-image-2",
            provider_currency="USD",
            submitted_attempts=2,
            submitted_images=4,
            total_cost_cents=54,
        ),
    )
