"""Daily health-check scheduling behind the model-routing Interface."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.model_routing.interface import ModelRouting
from app.model_routing.models import ModelRouteNotFound, RouteHealth, RouteHealthNotFound


class RouteHealthScheduler:
    """Run due route probes while preserving the last completed status between checks."""

    def __init__(
        self,
        routing: ModelRouting,
        *,
        clock: Callable[[], datetime] | None = None,
        interval: timedelta = timedelta(hours=24),
    ) -> None:
        if interval <= timedelta(0):
            raise ValueError("health check interval must be positive")
        self._routing = routing
        self._clock = clock or (lambda: datetime.now(UTC))
        self._interval = interval

    def run_due(self) -> tuple[RouteHealth, ...]:
        """Probe routes whose previous completed check is at least one interval old."""
        now = self._clock()
        enabled_provider_ids = {provider.provider_id for provider in self._routing.list_providers() if provider.enabled}
        completed: list[RouteHealth] = []
        for route in self._routing.list_routes():
            if route.provider_id not in enabled_provider_ids:
                continue
            try:
                previous = self._routing.route_health(route.route_id)
            except RouteHealthNotFound:
                previous = None
            if previous is not None and now - previous.checked_at < self._interval:
                continue
            try:
                completed.append(self._routing.check_route(route.route_id))
            except ModelRouteNotFound:
                continue
        return tuple(completed)
