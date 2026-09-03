"""Coordinator and immutable snapshots for Xray service groups."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import MappingProxyType
from typing import Any

from .api import (
    BalancerResult,
    GrpcXrayApi,
    OutboundStatus,
    StatsSnapshot,
    XrayApiError,
    normalize_grpc_error,
)
from .const import (
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    SERVICE_OBSERVATORY,
    SERVICE_ROUTING,
    SERVICE_STATS,
    STATUS_DEGRADED,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    STATUS_UNKNOWN,
)

try:
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
except ImportError:  # pragma: no cover - pure tests use this fallback.
    class UpdateFailed(Exception):
        """Fallback update error."""

    class DataUpdateCoordinator:  # type: ignore[no-redef]
        def __init__(self, hass=None, logger=None, name="", update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = False
            self._listeners: list[Callable[[], None]] = []

        async def async_config_entry_first_refresh(self):
            self.data = await self._async_update_data()
            self.last_update_success = True
            return self.data

        async def async_refresh(self):
            try:
                self.data = await self._async_update_data()
                self.last_update_success = True
            except Exception:
                self.last_update_success = False
                raise
            for listener in tuple(self._listeners):
                listener()

        def async_add_listener(self, listener):
            self._listeners.append(listener)
            return lambda: self._listeners.remove(listener)

        async def async_shutdown(self):
            return None


_LOGGER = logging.getLogger(__name__)
_ERROR_LOG_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class GroupStatus:
    """Availability and sanitized error for one service group."""

    available: bool
    error: str | None = None


@dataclass(frozen=True, slots=True)
class XraySnapshot:
    """Immutable coordinator snapshot published atomically after each poll."""

    outbounds: Mapping[str, OutboundStatus] = field(default_factory=dict)
    observed_outbound_tags: frozenset[str] = frozenset()
    balancers: Mapping[str, BalancerResult] = field(default_factory=dict)
    stats: StatsSnapshot | None = None
    reset_counters: frozenset[str] = frozenset()
    api_available: bool = False
    observatory: GroupStatus = field(default_factory=lambda: GroupStatus(False))
    stats_status: GroupStatus = field(default_factory=lambda: GroupStatus(False))
    routing: GroupStatus = field(default_factory=lambda: GroupStatus(True))
    last_successful_update: datetime | None = None
    generation: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "outbounds", MappingProxyType(dict(self.outbounds)))
        object.__setattr__(self, "balancers", MappingProxyType(dict(self.balancers)))
        object.__setattr__(self, "observed_outbound_tags", frozenset(self.observed_outbound_tags))
        object.__setattr__(self, "reset_counters", frozenset(self.reset_counters))


class XrayCoordinator(DataUpdateCoordinator):
    """Poll Observatory, Stats and Routing concurrently over one API client."""

    def __init__(
        self,
        host: str = "",
        port: int = DEFAULT_PORT,
        *,
        balancer_tags: tuple[str, ...] | list[str] = (),
        api: GrpcXrayApi | Any | None = None,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
        now_fn: Callable[[], datetime] | None = None,
        hass: Any | None = None,
        known_outbound_tags: tuple[str, ...] | list[str] = (),
        on_outbound_tags: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"xray_api_{host}:{port}",
            update_interval=update_interval,
        )
        self.host = host
        self.port = int(port)
        self.balancer_tags = tuple(dict.fromkeys(str(tag) for tag in balancer_tags if str(tag)))
        self.api = api or GrpcXrayApi(host, self.port)
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._known_outbound_tags = {str(tag) for tag in known_outbound_tags if str(tag)}
        self._on_outbound_tags = on_outbound_tags
        self._generation = 0
        self._last_snapshot = XraySnapshot()
        self._last_error_log: dict[str, float] = {}

    @property
    def snapshot(self) -> XraySnapshot:
        return self.data if isinstance(self.data, XraySnapshot) else self._last_snapshot

    @property
    def known_outbound_tags(self) -> frozenset[str]:
        """Return all tags seen in Observatory, including retained tags."""
        return frozenset(self._known_outbound_tags)

    @property
    def overall_status(self) -> str:
        snapshot = self.snapshot
        if snapshot.last_successful_update is None:
            return STATUS_UNKNOWN
        if not snapshot.api_available:
            return STATUS_OFFLINE
        groups_failed = (
            not snapshot.observatory.available
            or not snapshot.stats_status.available
            or (bool(self.balancer_tags) and not snapshot.routing.available)
        )
        dead_outbound = any(
            not snapshot.outbounds[tag].alive
            for tag in snapshot.observed_outbound_tags
            if tag in snapshot.outbounds
        )
        return STATUS_DEGRADED if groups_failed or dead_outbound else STATUS_ONLINE

    async def _group_call(self, name: str, call: Awaitable[Any]) -> Any:
        try:
            return await call
        except asyncio.CancelledError:
            raise
        except (
            XrayApiError,
            asyncio.TimeoutError,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as error:
            normalized = normalize_grpc_error(error)
            now = time.monotonic()
            if now - self._last_error_log.get(name, 0.0) >= _ERROR_LOG_INTERVAL:
                _LOGGER.warning(
                    "Xray API %s group failed: error=%s",
                    name,
                    type(normalized).__name__,
                )
                self._last_error_log[name] = now
            return normalized

    async def _async_update_data(self) -> XraySnapshot:
        """Fetch a new immutable snapshot while retaining failed-group data."""
        tasks: list[Awaitable[Any]] = [
            self._group_call(SERVICE_OBSERVATORY, self.api.async_get_observatory()),
            self._group_call(SERVICE_STATS, self.api.async_get_stats()),
        ]
        if self.balancer_tags:
            tasks.append(
                self._group_call(
                    SERVICE_ROUTING,
                    self._async_get_balancers(),
                )
            )
        results = await asyncio.gather(*tasks)
        observatory_result, stats_result = results[:2]
        routing_result = results[2] if self.balancer_tags else {}
        previous = self._last_snapshot

        observatory_ok = not isinstance(observatory_result, BaseException)
        stats_ok = not isinstance(stats_result, BaseException)
        routing_ok = not isinstance(routing_result, BaseException)
        if routing_ok and self.balancer_tags:
            routing_ok = set(routing_result) == set(self.balancer_tags)
        outbounds = dict(previous.outbounds)
        if observatory_ok:
            outbounds.update(observatory_result)
            new_known_tags = self._known_outbound_tags | set(observatory_result)
            if new_known_tags != self._known_outbound_tags:
                self._known_outbound_tags = new_known_tags
                if self._on_outbound_tags is not None:
                    self._on_outbound_tags(tuple(sorted(new_known_tags)))
        observed = frozenset(observatory_result) if observatory_ok else frozenset()
        stats = stats_result if stats_ok else previous.stats
        reset_counters = set(previous.reset_counters)
        if stats_ok and isinstance(stats_result, StatsSnapshot):
            previous_counters = previous.stats.counters if previous.stats is not None else {}
            for key, value in stats_result.counters.items():
                old_value = previous_counters.get(key)
                if old_value is not None and value < old_value:
                    reset_counters.add(key)
                elif key in reset_counters and (old_value is None or value >= old_value):
                    reset_counters.discard(key)
        balancers = dict(routing_result) if routing_ok else dict(previous.balancers)
        # Observatory and Stats are required for an endpoint to count as
        # reachable. Routing-only success must not mask a transport failure in
        # both required groups.
        any_success = observatory_ok or stats_ok
        all_failed = not any_success
        self._generation += 1
        successful_update = previous.last_successful_update
        if any_success and not all_failed:
            successful_update = self._now_fn()

        snapshot = XraySnapshot(
            outbounds=outbounds,
            observed_outbound_tags=observed,
            balancers=balancers,
            stats=stats,
            reset_counters=frozenset(reset_counters),
            api_available=not all_failed,
            observatory=GroupStatus(observatory_ok, self._error_name(observatory_result)),
            stats_status=GroupStatus(stats_ok, self._error_name(stats_result)),
            routing=GroupStatus(
                routing_ok if self.balancer_tags else True,
                self._error_name(routing_result),
            ),
            last_successful_update=successful_update,
            generation=self._generation,
        )
        self._last_snapshot = snapshot
        # Publish the unavailable snapshot even when HA marks this refresh as
        # failed, so entities can expose transport/group availability.
        self.data = snapshot
        if all_failed:
            # DataUpdateCoordinator treats this as a failed update, but the
            # snapshot remains available to entities for group-level state.
            raise UpdateFailed("Xray API endpoint unavailable")
        return snapshot

    async def _async_get_balancers(self) -> dict[str, BalancerResult]:
        results = await asyncio.gather(
            *(self.api.async_get_balancer(tag) for tag in self.balancer_tags),
            return_exceptions=True,
        )
        if all(isinstance(item, BaseException) for item in results):
            return results[0]  # type: ignore[return-value]
        return {
            tag: result
            for tag, result in zip(self.balancer_tags, results)
            if not isinstance(result, BaseException)
        }

    @staticmethod
    def _error_name(value: Any) -> str | None:
        if not isinstance(value, BaseException):
            return None
        return type(value).__name__

    async def async_shutdown(self) -> None:
        base_shutdown = getattr(super(), "async_shutdown", None)
        if base_shutdown is not None:
            result = base_shutdown()
            if hasattr(result, "__await__"):
                await result
        for manager in getattr(self, "_entity_managers", ()):
            shutdown = getattr(manager, "async_shutdown", None)
            if shutdown is not None:
                shutdown()
        close = getattr(self.api, "async_close", None)
        if close is not None:
            await close()

    def outbound_available(self, tag: str) -> bool:
        snapshot = self.snapshot
        return snapshot.observatory.available and tag in snapshot.observed_outbound_tags

    def traffic_available(self) -> bool:
        return self.snapshot.stats_status.available and self.snapshot.stats is not None

    def stats_available(self) -> bool:
        return self.traffic_available()

    def balancer_available(self, tag: str) -> bool:
        return (
            tag in self.balancer_tags
            and self.snapshot.routing.available
            and tag in self.snapshot.balancers
        )

    def counter(self, tag: str, direction: str) -> int | None:
        if not self.traffic_available() or self.snapshot.stats is None:
            return None
        key = f"outbound>>>{tag}>>>traffic>>>{direction}"
        if key in self.snapshot.reset_counters:
            return None
        return self.snapshot.stats.counters.get(key)
