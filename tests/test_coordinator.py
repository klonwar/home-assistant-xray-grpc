from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from custom_components.xray_api.api import (
    BalancerResult,
    OutboundStatus,
    StatsSnapshot,
    XrayConnectionError,
)
from custom_components.xray_api.coordinator import XrayCoordinator


def outbound(tag: str, alive: bool = True) -> OutboundStatus:
    return OutboundStatus(tag, alive, 10, "raw", None, None, None, None, None)


class FakeApi:
    def __init__(self):
        self.observatory = {"vm9": outbound("vm9")}
        self.stats = StatsSnapshot(
            uptime_seconds=12,
            counters={
                "outbound>>>vm9>>>traffic>>>uplink": 100,
                "outbound>>>vm9>>>traffic>>>downlink": 200,
            },
        )
        self.balancer = BalancerResult(
            "fallback", "vm9", ("vm9",), "", datetime.now(timezone.utc), {}
        )
        self.fail_observatory = False
        self.fail_stats = False
        self.fail_routing = False

    async def async_get_observatory(self):
        if self.fail_observatory:
            raise XrayConnectionError("down")
        return self.observatory

    async def async_get_stats(self):
        if self.fail_stats:
            raise XrayConnectionError("down")
        return self.stats

    async def async_get_balancer(self, tag):
        if self.fail_routing:
            raise XrayConnectionError("down")
        return self.balancer

    async def async_close(self):
        return None


def refresh(coordinator: XrayCoordinator):
    snapshot = asyncio.run(coordinator._async_update_data())
    coordinator.data = snapshot
    return snapshot


def test_successful_refresh_and_status() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api, balancer_tags=("fallback",))
    snapshot = refresh(coordinator)
    assert snapshot.api_available is True
    assert coordinator.overall_status == "online"
    assert coordinator.counter("vm9", "uplink") == 100
    assert coordinator.balancer_available("fallback")


def test_routing_failure_degrades_status_when_balancers_are_configured() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api, balancer_tags=("fallback",))
    refresh(coordinator)
    api.fail_routing = True
    refresh(coordinator)
    assert coordinator.snapshot.routing.available is False
    assert coordinator.overall_status == "degraded"


def test_balancer_availability_follows_options_tags() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api, balancer_tags=("fallback",))
    refresh(coordinator)
    assert coordinator.balancer_available("fallback") is True
    coordinator.balancer_tags = ()
    assert coordinator.balancer_available("fallback") is False


def test_partial_observatory_failure_retains_data_but_marks_group_unavailable() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api)
    refresh(coordinator)
    api.fail_observatory = True
    snapshot = refresh(coordinator)
    assert snapshot.outbounds["vm9"].alive is True
    assert snapshot.observatory.available is False
    assert coordinator.outbound_available("vm9") is False
    assert coordinator.counter("vm9", "uplink") == 100
    assert coordinator.overall_status == "degraded"
    assert snapshot.last_successful_update is not None


def test_dead_outbound_is_degraded_and_disappearance_is_retained() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api)
    refresh(coordinator)
    api.observatory = {"vm9": outbound("vm9", alive=False)}
    refresh(coordinator)
    assert coordinator.overall_status == "degraded"
    api.observatory = {}
    snapshot = refresh(coordinator)
    assert "vm9" in snapshot.outbounds
    assert coordinator.outbound_available("vm9") is False


def test_counter_reset_temporarily_marks_counter_unavailable() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api)
    refresh(coordinator)
    api.stats = StatsSnapshot(
        uptime_seconds=1,
        counters={"outbound>>>vm9>>>traffic>>>uplink": 3},
    )
    refresh(coordinator)
    assert coordinator.counter("vm9", "uplink") is None
    api.stats = StatsSnapshot(
        uptime_seconds=2,
        counters={"outbound>>>vm9>>>traffic>>>uplink": 4},
    )
    refresh(coordinator)
    assert coordinator.counter("vm9", "uplink") == 4


def test_endpoint_down_raises_but_previous_snapshot_survives() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api)
    refresh(coordinator)
    previous = coordinator.snapshot
    api.fail_observatory = True
    api.fail_stats = True
    try:
        refresh(coordinator)
    except Exception as error:
        assert type(error).__name__ == "UpdateFailed"
    assert coordinator.snapshot.outbounds == previous.outbounds
    assert coordinator.snapshot.api_available is False
    assert coordinator.overall_status == "offline"


def test_stats_only_failure_keeps_observatory_data_but_hides_traffic() -> None:
    api = FakeApi()
    coordinator = XrayCoordinator(api=api)
    refresh(coordinator)
    api.fail_stats = True
    snapshot = refresh(coordinator)
    assert snapshot.observatory.available is True
    assert snapshot.stats_status.available is False
    assert coordinator.outbound_available("vm9") is True
    assert coordinator.counter("vm9", "uplink") is None


def test_successful_observatory_refresh_reports_tags_for_persistence() -> None:
    api = FakeApi()
    persisted: list[tuple[str, ...]] = []
    coordinator = XrayCoordinator(api=api, on_outbound_tags=persisted.append)

    refresh(coordinator)

    assert persisted == [("vm9",)]
    assert coordinator.known_outbound_tags == frozenset({"vm9"})
