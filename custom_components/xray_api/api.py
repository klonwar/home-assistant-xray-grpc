"""Read-only Xray gRPC client and protobuf-to-domain mapping."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from .const import DEFAULT_RPC_TIMEOUT
from .proto import (
    observatory_pb2,
    observatory_pb2_grpc,
    routing_pb2,
    routing_pb2_grpc,
    stats_pb2,
    stats_pb2_grpc,
)

try:
    import grpc
except ImportError:  # pragma: no cover - Home Assistant supplies grpcio.
    grpc = None


_LOGGER = logging.getLogger(__name__)
OUTBOUND_STATS_PREFIX = "outbound>>>"


class XrayApiError(Exception):
    """Base class for normalized Xray API failures."""


class XrayTimeoutError(XrayApiError):
    """The RPC deadline expired."""


class XrayConnectionError(XrayApiError):
    """The endpoint could not be reached."""


class XrayUnimplementedError(XrayApiError):
    """The Xray build does not expose a requested service or method."""


class XrayServiceError(XrayApiError):
    """A non-transport service failure occurred."""


@dataclass(frozen=True, slots=True)
class HealthPingMeasurement:
    """Observatory health-ping measurements, documented by Xray as ms."""

    all: int = 0
    fail: int = 0
    deviation: int = 0
    average: int = 0
    max: int = 0
    min: int = 0


@dataclass(frozen=True, slots=True)
class OutboundStatus:
    """Normalized Observatory status for one outbound tag."""

    outbound_tag: str
    alive: bool
    delay_ms: int | None
    last_error_reason: str
    last_seen_time: datetime | None
    last_try_time: datetime | None
    last_seen_time_raw: int | None
    last_try_time_raw: int | None
    health_ping: HealthPingMeasurement | None


@dataclass(frozen=True, slots=True)
class StatsSnapshot:
    """Normalized Xray uptime and outbound byte counters."""

    uptime_seconds: int
    counters: Mapping[str, int]
    reset: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "counters", MappingProxyType(dict(self.counters)))


@dataclass(frozen=True, slots=True)
class BalancerResult:
    """Normalized strategy candidate returned for a balancer tag."""

    balancer_tag: str
    principle_target: str
    principle_targets: tuple[str, ...]
    override: str
    query_time: datetime
    raw_response: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_response", MappingProxyType(dict(self.raw_response)))


def _attr(value: Any, *names: str, default: Any = None) -> Any:
    """Read the first present attribute/key from protobuf or test objects."""
    for name in names:
        if isinstance(value, Mapping) and name in value:
            return value[name]
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _field_present(value: Any, *names: str) -> bool:
    """Check whether an optional protobuf/message field was supplied."""
    if isinstance(value, Mapping):
        return any(name in value and value[name] is not None for name in names)
    has_field = getattr(value, "HasField", None)
    if callable(has_field):
        for name in names:
            try:
                if has_field(name):
                    return True
            except (TypeError, ValueError):
                continue
        return False
    return any(_attr(value, name, default=None) is not None for name in names)


def _timestamp(value: Any) -> tuple[datetime | None, int | None]:
    """Convert Xray Unix seconds (or defensive Unix milliseconds) to UTC."""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None, None
    if raw <= 0:
        return None, raw
    seconds = raw / 1000 if abs(raw) >= 10_000_000_000 else raw
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc), raw
    except (OverflowError, OSError, ValueError):
        return None, raw


def _health_ping(message: Any) -> HealthPingMeasurement | None:
    if message is None:
        return None
    values = {name: int(_attr(message, name, default=0) or 0) for name in (
        "all", "fail", "deviation", "average", "max", "min"
    )}
    return HealthPingMeasurement(**values)


def map_outbound_status(message: Any) -> OutboundStatus | None:
    """Map one Observatory protobuf message without rewriting raw errors."""
    tag = str(_attr(message, "outbound_tag", "outboundTag", default="") or "")
    if not tag:
        return None
    last_seen, last_seen_raw = _timestamp(
        _attr(message, "last_seen_time", "lastSeenTime", default=0)
    )
    last_try, last_try_raw = _timestamp(
        _attr(message, "last_try_time", "lastTryTime", default=0)
    )
    delay = _attr(message, "delay", default=None)
    try:
        delay_ms = int(delay) if delay is not None else None
    except (TypeError, ValueError):
        delay_ms = None
    health_ping_message = _attr(message, "health_ping", "healthPing", default=None)
    if not _field_present(message, "health_ping", "healthPing"):
        health_ping_message = None
    return OutboundStatus(
        outbound_tag=tag,
        alive=bool(_attr(message, "alive", default=False)),
        delay_ms=delay_ms,
        last_error_reason=str(
            _attr(message, "last_error_reason", "lastErrorReason", default="") or ""
        ),
        last_seen_time=last_seen,
        last_try_time=last_try,
        last_seen_time_raw=last_seen_raw,
        last_try_time_raw=last_try_raw,
        health_ping=_health_ping(health_ping_message),
    )


def map_observatory_response(response: Any) -> dict[str, OutboundStatus]:
    """Map an Observatory response keyed by its original outbound tag."""
    observation = _attr(response, "status", default=response)
    statuses = _attr(observation, "status", default=None)
    if statuses is None and isinstance(observation, Iterable) and not isinstance(
        observation, (str, bytes, Mapping)
    ):
        statuses = observation
    statuses = statuses or ()
    result: dict[str, OutboundStatus] = {}
    for item in statuses:
        mapped = map_outbound_status(item)
        if mapped is not None:
            result[mapped.outbound_tag] = mapped
    return result


def map_stats_response(sys_response: Any, query_response: Any) -> StatsSnapshot:
    """Map Stats responses and retain the explicit non-reset query contract."""
    uptime = _attr(sys_response, "Uptime", "uptime", default=0)
    try:
        uptime_seconds = max(0, int(uptime or 0))
    except (TypeError, ValueError):
        uptime_seconds = 0
    stats = _attr(query_response, "stat", "stats", default=()) or ()
    counters: dict[str, int] = {}
    for item in stats:
        name = str(_attr(item, "name", default="") or "")
        if not name.startswith(OUTBOUND_STATS_PREFIX):
            continue
        try:
            counters[name] = int(_attr(item, "value", default=0) or 0)
        except (TypeError, ValueError):
            continue
    return StatsSnapshot(uptime_seconds=uptime_seconds, counters=counters, reset=False)


def _raw_message(value: Any) -> Mapping[str, Any]:
    """Return a diagnostic-safe raw mapping for protobuf or test messages."""
    try:
        from google.protobuf.json_format import MessageToDict

        if hasattr(value, "DESCRIPTOR"):
            return MessageToDict(value, preserving_proto_field_name=True)
    except (ImportError, TypeError, ValueError):
        pass
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return {
        key: val
        for key in ("balancer", "override", "principle_target", "target", "tag")
        if (val := _attr(value, key, default=None)) is not None
    }


def map_balancer_response(
    tag: str, response: Any, *, query_time: datetime | None = None
) -> BalancerResult:
    """Map Routing.GetBalancerInfo while retaining the raw response."""
    balancer = _attr(response, "balancer", default=response)
    principle = _attr(balancer, "principle_target", "principleTarget", default=None)
    targets = _attr(principle, "tag", "tags", default=()) if principle is not None else ()
    if isinstance(targets, str):
        targets = (targets,) if targets else ()
    targets = tuple(str(item) for item in (targets or ()) if str(item))
    override_info = _attr(balancer, "override", default=None)
    override = str(_attr(override_info, "target", default="") or "")
    return BalancerResult(
        balancer_tag=tag,
        principle_target=targets[0] if targets else "",
        principle_targets=targets,
        override=override,
        query_time=query_time or datetime.now(timezone.utc),
        raw_response=_raw_message(response),
    )


def normalize_grpc_error(error: BaseException) -> XrayApiError:
    """Normalize grpc/asyncio errors without exposing endpoint details."""
    if isinstance(error, XrayApiError):
        return error
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return XrayTimeoutError("Xray API request timed out")
    code = None
    try:
        code = (
            error.code()
            if callable(getattr(error, "code", None))
            else getattr(error, "code", None)
        )
    except Exception:  # pragma: no cover - defensive around third-party errors.
        code = None
    code_name = str(getattr(code, "name", code) or "").upper()
    if "UNIMPLEMENTED" in code_name:
        return XrayUnimplementedError("Xray API method is unimplemented")
    if any(name in code_name for name in ("UNAVAILABLE", "CONNECTION", "REFUSED")):
        return XrayConnectionError("Xray API endpoint is unavailable")
    if "DEADLINE" in code_name or "TIMEOUT" in code_name:
        return XrayTimeoutError("Xray API request timed out")
    return XrayServiceError("Xray API service request failed")


async def _await(value: Any) -> Any:
    return await value if hasattr(value, "__await__") else value


class GrpcXrayApi:
    """Shared-channel client for the read-only Xray service groups."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        channel: Any | None = None,
        observatory_stub: Any | None = None,
        stats_stub: Any | None = None,
        routing_stub: Any | None = None,
        channel_factory: Any | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.target = (
            f"[{host}]:{self.port}"
            if ":" in host and not host.startswith("[")
            else f"{host}:{self.port}"
        )
        self._owns_channel = channel is None
        if channel is None and channel_factory is not None:
            channel = channel_factory(self.target)
        if channel is None and grpc is not None:
            channel = grpc.aio.insecure_channel(self.target)
        self.channel = channel
        self._observatory = observatory_stub or (
            observatory_pb2_grpc.ObservatoryServiceStub(channel) if channel is not None else None
        )
        self._stats = stats_stub or (
            stats_pb2_grpc.StatsServiceStub(channel) if channel is not None else None
        )
        self._routing = routing_stub or (
            routing_pb2_grpc.RoutingServiceStub(channel) if channel is not None else None
        )

    async def async_close(self) -> None:
        close = getattr(self.channel, "close", None)
        if close is not None:
            await _await(close())

    async def _call(self, method: Any, request: Any, timeout: float) -> Any:
        if method is None:
            raise XrayUnimplementedError("Xray API method is unavailable")
        try:
            return await _await(method(request, timeout=timeout))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            normalized = normalize_grpc_error(error)
            if normalized is not error:
                raise normalized from error
            raise

    async def async_get_observatory(
        self, timeout: float = DEFAULT_RPC_TIMEOUT
    ) -> dict[str, OutboundStatus]:
        response = await self._call(
            getattr(self._observatory, "GetOutboundStatus", None),
            observatory_pb2.GetOutboundStatusRequest(),
            timeout,
        )
        return map_observatory_response(response)

    async def async_get_stats(self, timeout: float = DEFAULT_RPC_TIMEOUT) -> StatsSnapshot:
        sys_response, query_response = await asyncio.gather(
            self._call(
                getattr(self._stats, "GetSysStats", None),
                stats_pb2.SysStatsRequest(),
                timeout,
            ),
            self._call(
                getattr(self._stats, "QueryStats", None),
                stats_pb2.QueryStatsRequest(pattern=OUTBOUND_STATS_PREFIX, reset=False),
                timeout,
            ),
        )
        return map_stats_response(sys_response, query_response)

    async def async_get_balancer(
        self, tag: str, timeout: float = DEFAULT_RPC_TIMEOUT
    ) -> BalancerResult:
        queried_at = datetime.now(timezone.utc)
        response = await self._call(
            getattr(self._routing, "GetBalancerInfo", None),
            routing_pb2.GetBalancerInfoRequest(tag=tag),
            timeout,
        )
        return map_balancer_response(tag, response, query_time=queried_at)

    async def async_validate(self, *, require_routing: bool, timeout: float) -> None:
        """Require Observatory and Stats, plus Routing when configured."""
        await asyncio.gather(
            self.async_get_observatory(timeout),
            self.async_get_stats(timeout),
        )
        if require_routing:
            # A tag is supplied by Config Flow and checked separately there.
            # Calling the service with an empty tag still validates support.
            await self.async_get_balancer("", timeout)
