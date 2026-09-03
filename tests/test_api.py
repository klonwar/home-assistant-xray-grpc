from __future__ import annotations

import asyncio
from datetime import timezone

from custom_components.xray_api.api import (
    GrpcXrayApi,
    XrayConnectionError,
    XrayTimeoutError,
    XrayUnimplementedError,
    map_balancer_response,
    map_observatory_response,
    map_stats_response,
    normalize_grpc_error,
)
from custom_components.xray_api.proto.observatory_pb2 import (
    GetOutboundStatusResponse,
    HealthPingMeasurementResult,
    ObservationResult,
    OutboundStatus,
)
from custom_components.xray_api.proto.routing_pb2 import (
    BalancerMsg,
    GetBalancerInfoResponse,
    OverrideInfo,
    PrincipleTargetInfo,
)
from custom_components.xray_api.proto.stats_pb2 import (
    QueryStatsResponse,
    Stat,
    SysStatsResponse,
)


def test_observatory_mapping_preserves_raw_error_and_units() -> None:
    response = GetOutboundStatusResponse(
        status=ObservationResult(
            status=[
                OutboundStatus(
                    outbound_tag="vm9-vless",
                    alive=False,
                    delay=123,
                    last_error_reason="opaque Xray text",
                    last_seen_time=1_648_477_189,
                    last_try_time=1_648_477_190,
                    health_ping=HealthPingMeasurementResult(
                        all=5, fail=1, deviation=2, average=30, max=55, min=20
                    ),
                )
            ]
        )
    )
    mapped = map_observatory_response(response)["vm9-vless"]
    assert mapped.delay_ms == 123
    assert mapped.last_error_reason == "opaque Xray text"
    assert mapped.last_seen_time is not None
    assert mapped.last_seen_time.tzinfo == timezone.utc
    assert mapped.last_seen_time_raw == 1_648_477_189
    assert mapped.health_ping is not None
    assert mapped.health_ping.average == 30


def test_observatory_mapping_keeps_absent_health_ping_unknown() -> None:
    response = GetOutboundStatusResponse(
        status=ObservationResult(status=[OutboundStatus(outbound_tag="direct")])
    )

    mapped = map_observatory_response(response)["direct"]

    assert mapped.health_ping is None


def test_stats_mapping_uses_outbound_prefix_and_reset_false() -> None:
    mapped = map_stats_response(
        SysStatsResponse(Uptime=42),
        QueryStatsResponse(
            stat=[
                Stat(name="outbound>>>vm9-vless>>>traffic>>>uplink", value=100),
                Stat(name="outbound>>>vm9-vless>>>traffic>>>downlink", value=200),
                Stat(name="inbound>>>ignored>>>traffic>>>uplink", value=999),
            ]
        ),
    )
    assert mapped.uptime_seconds == 42
    assert mapped.reset is False
    assert mapped.counters["outbound>>>vm9-vless>>>traffic>>>uplink"] == 100
    assert all(key.startswith("outbound>>>") for key in mapped.counters)


def test_routing_mapping_empty_target_is_neutral_and_raw_is_kept() -> None:
    mapped = map_balancer_response(
        "fallback",
        GetBalancerInfoResponse(
            balancer=BalancerMsg(
                principle_target=PrincipleTargetInfo(tag=[]),
                override=OverrideInfo(target="direct"),
            )
        ),
    )
    assert mapped.principle_target == ""
    assert mapped.principle_targets == ()
    assert mapped.override == "direct"
    assert mapped.raw_response


def test_grpc_error_normalization() -> None:
    class Error:
        def __init__(self, code):
            self._code = code

        def code(self):
            return self._code

    assert isinstance(normalize_grpc_error(asyncio.TimeoutError()), XrayTimeoutError)
    assert isinstance(normalize_grpc_error(Error("UNIMPLEMENTED")), XrayUnimplementedError)
    assert isinstance(normalize_grpc_error(Error("UNAVAILABLE")), XrayConnectionError)


def test_api_uses_only_read_only_methods_and_reset_false() -> None:
    calls: list[tuple[str, object]] = []

    class Observatory:
        async def GetOutboundStatus(self, request, timeout):
            calls.append(("observatory", request))
            return GetOutboundStatusResponse(status=ObservationResult())

    class Stats:
        async def GetSysStats(self, request, timeout):
            calls.append(("sys", request))
            return SysStatsResponse(Uptime=1)

        async def QueryStats(self, request, timeout):
            calls.append(("query", request))
            return QueryStatsResponse()

    api = GrpcXrayApi("xray.local", 10085, observatory_stub=Observatory(), stats_stub=Stats())
    asyncio.run(api.async_get_observatory())
    asyncio.run(api.async_get_stats())
    assert {name for name, _ in calls} == {"observatory", "sys", "query"}
    query = next(request for name, request in calls if name == "query")
    assert query.reset is False
    assert query.pattern == "outbound>>>"
