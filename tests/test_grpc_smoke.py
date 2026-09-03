from __future__ import annotations

import asyncio

import grpc

from custom_components.xray_api.api import GrpcXrayApi
from custom_components.xray_api.proto.observatory_pb2 import (
    GetOutboundStatusResponse,
    ObservationResult,
    OutboundStatus,
)
from custom_components.xray_api.proto.observatory_pb2_grpc import (
    ObservatoryServiceServicer,
    add_ObservatoryServiceServicer_to_server,
)
from custom_components.xray_api.proto.routing_pb2 import (
    BalancerMsg,
    GetBalancerInfoResponse,
    PrincipleTargetInfo,
)
from custom_components.xray_api.proto.routing_pb2_grpc import (
    RoutingServiceServicer,
    add_RoutingServiceServicer_to_server,
)
from custom_components.xray_api.proto.stats_pb2 import (
    QueryStatsResponse,
    Stat,
    SysStatsResponse,
)
from custom_components.xray_api.proto.stats_pb2_grpc import (
    StatsServiceServicer,
    add_StatsServiceServicer_to_server,
)


def test_generated_stubs_round_trip_against_fake_grpc_server() -> None:
    async def run() -> None:
        class Obs(ObservatoryServiceServicer):
            async def GetOutboundStatus(self, request, context):  # noqa: N802
                return GetOutboundStatusResponse(
                    status=ObservationResult(
                        status=[OutboundStatus(outbound_tag="fake", alive=True, delay=7)]
                    )
                )

        class Stats(StatsServiceServicer):
            async def GetSysStats(self, request, context):  # noqa: N802
                return SysStatsResponse(Uptime=9)

            async def QueryStats(self, request, context):  # noqa: N802
                return QueryStatsResponse(
                    stat=[
                        Stat(
                            name="outbound>>>fake>>>traffic>>>uplink",
                            value=11,
                        )
                    ]
                )

        class Routing(RoutingServiceServicer):
            async def GetBalancerInfo(self, request, context):  # noqa: N802
                return GetBalancerInfoResponse(
                    balancer=BalancerMsg(
                        principle_target=PrincipleTargetInfo(tag=["fake"])
                    )
                )

        server = grpc.aio.server()
        add_ObservatoryServiceServicer_to_server(Obs(), server)
        add_StatsServiceServicer_to_server(Stats(), server)
        add_RoutingServiceServicer_to_server(Routing(), server)
        port = server.add_insecure_port("127.0.0.1:0")
        await server.start()
        api = GrpcXrayApi("127.0.0.1", port)
        try:
            outbounds = await api.async_get_observatory()
            stats = await api.async_get_stats()
            balancer = await api.async_get_balancer("fallback")
            assert outbounds["fake"].delay_ms == 7
            assert stats.uptime_seconds == 9
            assert stats.counters["outbound>>>fake>>>traffic>>>uplink"] == 11
            assert balancer.principle_target == "fake"
        finally:
            await api.async_close()
            await server.stop(0)

    asyncio.run(run())

