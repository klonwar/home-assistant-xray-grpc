from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.xray_api.api import BalancerResult, OutboundStatus, StatsSnapshot
from custom_components.xray_api.binary_sensor import ApiAvailableBinarySensor
from custom_components.xray_api.coordinator import XrayCoordinator
from custom_components.xray_api.entity import stable_unique_id
from custom_components.xray_api.sensor import (
    BalancerTargetSensor,
    OutboundTrafficSensor,
    _DynamicBalancerSensors,
    _DynamicOutboundSensors,
)


def test_unique_id_encodes_original_tag_without_collisions() -> None:
    first = stable_unique_id("entry", "outbound_alive", "a/b")
    second = stable_unique_id("entry", "outbound_alive", "a b")
    assert first != second
    assert first.startswith("xray_api_entry_outbound_alive_")


def test_balancer_entity_becomes_unavailable_after_options_tag_removed() -> None:
    api = SimpleNamespace(
        async_get_observatory=lambda: asyncio.sleep(0, result={}),
        async_get_stats=lambda: asyncio.sleep(0, result=StatsSnapshot(1, {})),
        async_get_balancer=lambda _tag: asyncio.sleep(
            0,
            result=BalancerResult(
                "fallback", "vm9", ("vm9",), "", datetime.now(timezone.utc), {}
            ),
        ),
    )
    coordinator = XrayCoordinator(api=api, balancer_tags=("fallback",))
    coordinator.data = coordinator._last_snapshot = coordinator._last_snapshot.__class__(
        balancers={
            "fallback": BalancerResult(
                "fallback", "vm9", ("vm9",), "", datetime.now(timezone.utc), {}
            )
        },
        stats=StatsSnapshot(1, {}),
        api_available=True,
        last_successful_update=datetime.now(timezone.utc),
        routing=coordinator._last_snapshot.routing.__class__(True),
        observatory=coordinator._last_snapshot.observatory.__class__(True),
        stats_status=coordinator._last_snapshot.stats_status.__class__(True),
    )
    entry = SimpleNamespace(entry_id="entry")
    entity = BalancerTargetSensor(coordinator, entry, "fallback")
    assert entity.available is True
    coordinator.balancer_tags = ()
    assert entity.available is False


def test_balancer_manager_recreates_tag_after_remove_and_readd() -> None:
    coordinator = XrayCoordinator(api=SimpleNamespace(), balancer_tags=("a",))
    added = []
    manager = _DynamicBalancerSensors(coordinator, SimpleNamespace(entry_id="entry"), added.extend)
    manager._update()
    assert len(added) == 1
    coordinator.balancer_tags = ()
    manager._update()
    assert "a" not in manager.seen
    coordinator.balancer_tags = ("a",)
    manager._update()
    assert len(added) == 2


def test_outbound_traffic_sensor_unavailable_when_observatory_loses_tag() -> None:
    coordinator = XrayCoordinator(api=SimpleNamespace())
    coordinator.data = coordinator._last_snapshot = coordinator._last_snapshot.__class__(
        outbounds={"vm9": OutboundStatus("vm9", True, 10, "", None, None, None, None, None)},
        stats=StatsSnapshot(1, {"outbound>>>vm9>>>traffic>>>uplink": 4}),
        api_available=True,
        observed_outbound_tags=frozenset(),
        last_successful_update=datetime.now(timezone.utc),
        routing=coordinator._last_snapshot.routing.__class__(True),
        observatory=coordinator._last_snapshot.observatory.__class__(False),
        stats_status=coordinator._last_snapshot.stats_status.__class__(True),
    )
    sensor = OutboundTrafficSensor(coordinator, SimpleNamespace(entry_id="entry"), "vm9", "uplink")
    assert sensor.available is False


def test_dynamic_outbound_sensors_restore_persisted_tags_before_first_snapshot() -> None:
    coordinator = XrayCoordinator(api=SimpleNamespace(), known_outbound_tags=("vm9",))
    added = []
    manager = _DynamicOutboundSensors(coordinator, SimpleNamespace(entry_id="entry"), added.extend)

    manager._update()

    assert len(added) == 3
    assert {entity.tag for entity in added} == {"vm9"}
    assert all(entity.available is False for entity in added)


def test_api_available_entity_is_visible_before_first_successful_snapshot() -> None:
    coordinator = XrayCoordinator(api=SimpleNamespace())

    entity = ApiAvailableBinarySensor(coordinator, SimpleNamespace(entry_id="entry"))

    assert entity.available is True
    assert entity.is_on is False
