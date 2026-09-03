"""Xray sensor entities."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .const import (
    ATTR_BALANCER_TAG,
    ATTR_OUTBOUND_TAG,
    ATTR_OVERRIDE,
    ATTR_QUERY_TIME,
    ATTR_RAW_RESPONSE,
)
from .coordinator import XrayCoordinator
from .entity import XrayEntity, outbound_attributes, safe_raw_attributes

try:
    from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
except ImportError:  # pragma: no cover
    from .entity import Entity as _BaseEntity

    class SensorEntity(_BaseEntity):  # type: ignore[no-redef]
        pass

    class SensorDeviceClass:
        DATA_SIZE = "data_size"
        DURATION = "duration"
        TIMESTAMP = "timestamp"

    class SensorStateClass:
        TOTAL_INCREASING = "total_increasing"


def _coordinator(hass: Any, entry: Any) -> XrayCoordinator:
    return hass.data["xray_api"][entry.entry_id]


class XraySensor(XrayEntity, SensorEntity):
    """Base sensor with HA metadata fallback."""

    def __init__(self, coordinator, entry, kind, *, tag=None, name=None):
        XrayEntity.__init__(self, coordinator, entry, kind, tag)
        self._attr_name = name or kind.replace("_", " ").title()


class XrayStatusSensor(XraySensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "status", name="Status")
        self._attr_icon = "mdi:server-network"

    @property
    def native_value(self) -> str:
        return self.coordinator.overall_status

    @property
    def extra_state_attributes(self):
        snapshot = self.coordinator.snapshot
        return {
            "api_available": snapshot.api_available,
            "observatory_available": snapshot.observatory.available,
            "stats_available": snapshot.stats_status.available,
            "routing_available": snapshot.routing.available,
            "last_successful_update": snapshot.last_successful_update.isoformat()
            if snapshot.last_successful_update
            else None,
        }


class XrayUptimeSensor(XraySensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "uptime", name="Xray uptime")
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_class = SensorDeviceClass.DURATION

    @property
    def available(self) -> bool:
        return self.coordinator.stats_available()

    @property
    def native_value(self) -> int | None:
        stats = self.coordinator.snapshot.stats
        return stats.uptime_seconds if stats is not None else None


class XrayLastUpdateSensor(XraySensor):
    def __init__(self, coordinator, entry):
        super().__init__(
            coordinator,
            entry,
            "last_successful_update",
            name="Last successful update",
        )
        self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def available(self) -> bool:
        return self.coordinator.snapshot.last_successful_update is not None

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.snapshot.last_successful_update


class OutboundDelaySensor(XraySensor):
    def __init__(self, coordinator, entry, tag):
        super().__init__(coordinator, entry, "outbound_delay", tag=tag, name=f"{tag} delay")
        self._attr_native_unit_of_measurement = "ms"

    @property
    def available(self) -> bool:
        return self.coordinator.outbound_available(self.tag)

    @property
    def native_value(self) -> int | None:
        status = self.coordinator.snapshot.outbounds.get(self.tag)
        return status.delay_ms if status is not None else None

    @property
    def extra_state_attributes(self):
        status = self.coordinator.snapshot.outbounds.get(self.tag)
        return outbound_attributes(status) if status is not None else {ATTR_OUTBOUND_TAG: self.tag}


class OutboundTrafficSensor(XraySensor):
    def __init__(self, coordinator, entry, tag, direction):
        kind = f"outbound_{direction}"
        label = "uplink" if direction == "uplink" else "downlink"
        super().__init__(coordinator, entry, kind, tag=tag, name=f"{tag} {label}")
        self.direction = direction
        self._attr_device_class = SensorDeviceClass.DATA_SIZE
        self._attr_native_unit_of_measurement = "B"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def available(self) -> bool:
        return (
            self.coordinator.outbound_available(self.tag)
            and self.coordinator.counter(self.tag, self.direction) is not None
        )

    @property
    def native_value(self) -> int | None:
        return self.coordinator.counter(self.tag, self.direction)

    @property
    def extra_state_attributes(self):
        return {
            ATTR_OUTBOUND_TAG: self.tag,
            "counter_reset_behavior": "unavailable on missing/reset counter",
        }


class BalancerTargetSensor(XraySensor):
    def __init__(self, coordinator, entry, tag):
        super().__init__(
            coordinator,
            entry,
            "balancer_principle_target",
            tag=tag,
            name=f"{tag} principle target",
        )
        self._attr_icon = "mdi:source-branch"

    @property
    def available(self) -> bool:
        return self.coordinator.balancer_available(self.tag)

    @property
    def native_value(self) -> str:
        value = self.coordinator.snapshot.balancers.get(self.tag)
        return value.principle_target if value and value.principle_target else "none"

    @property
    def extra_state_attributes(self):
        value = self.coordinator.snapshot.balancers.get(self.tag)
        if value is None:
            return {ATTR_BALANCER_TAG: self.tag}
        return {
            ATTR_BALANCER_TAG: value.balancer_tag,
            "principle_targets": list(value.principle_targets),
            ATTR_OVERRIDE: value.override,
            ATTR_QUERY_TIME: value.query_time.isoformat(),
            ATTR_RAW_RESPONSE: safe_raw_attributes(value.raw_response),
        }


def _outbound_sensors(coordinator, entry, tag):
    return [
        OutboundDelaySensor(coordinator, entry, tag),
        OutboundTrafficSensor(coordinator, entry, tag, "uplink"),
        OutboundTrafficSensor(coordinator, entry, tag, "downlink"),
    ]


class _DynamicOutboundSensors:
    def __init__(self, coordinator, entry, add_entities: Callable[[list[Any]], None]):
        self.coordinator = coordinator
        self.entry = entry
        self.add_entities = add_entities
        self.seen: set[str] = set()
        self.unsub = coordinator.async_add_listener(self._update)

    def _update(self):
        new_tags = set(self.coordinator.known_outbound_tags) - self.seen
        if new_tags:
            self.seen.update(new_tags)
            self.add_entities([
                entity
                for tag in sorted(new_tags)
                for entity in _outbound_sensors(self.coordinator, self.entry, tag)
            ])

    def async_shutdown(self):
        if self.unsub is not None:
            self.unsub()
            self.unsub = None


class _DynamicBalancerSensors:
    """Reconcile configured balancer sensors across Options Flow updates."""

    def __init__(self, coordinator, entry, add_entities: Callable[[list[Any]], None]):
        self.coordinator = coordinator
        self.entry = entry
        self.add_entities = add_entities
        self.seen: set[str] = set()
        self.entities: dict[str, BalancerTargetSensor] = {}
        self.unsub = coordinator.async_add_listener(self._update)

    def _update(self):
        configured = set(self.coordinator.balancer_tags)
        new_tags = configured - self.seen
        if new_tags:
            self.seen.update(new_tags)
            new_entities = []
            for tag in sorted(new_tags):
                entity = BalancerTargetSensor(self.coordinator, self.entry, tag)
                self.entities[tag] = entity
                new_entities.append(entity)
            self.add_entities(new_entities)
        for tag in set(self.entities) - configured:
            entity = self.entities.pop(tag)
            self.seen.discard(tag)
            remove = getattr(entity, "async_remove", None)
            if remove is not None:
                result = remove()
                if inspect.isawaitable(result):
                    create_task = getattr(
                        getattr(self.coordinator, "hass", None),
                        "async_create_task",
                        None,
                    )
                    if create_task is not None:
                        create_task(result)

    def async_shutdown(self):
        if self.unsub is not None:
            self.unsub()
            self.unsub = None


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Callable):
    """Set up core, dynamic outbound, and configured balancer sensors."""
    coordinator = _coordinator(hass, entry)
    entities: list[Any] = [
        XrayStatusSensor(coordinator, entry),
        XrayUptimeSensor(coordinator, entry),
        XrayLastUpdateSensor(coordinator, entry),
    ]
    manager = _DynamicOutboundSensors(coordinator, entry, async_add_entities)
    manager.seen.update(coordinator.known_outbound_tags)
    entities.extend(
        entity
        for tag in sorted(coordinator.known_outbound_tags)
        for entity in _outbound_sensors(coordinator, entry, tag)
    )
    balancer_manager = _DynamicBalancerSensors(coordinator, entry, async_add_entities)
    initial_balancers = {
        tag: BalancerTargetSensor(coordinator, entry, tag)
        for tag in coordinator.balancer_tags
    }
    balancer_manager.seen.update(initial_balancers)
    balancer_manager.entities.update(initial_balancers)
    entities.extend(initial_balancers.values())
    managers = getattr(coordinator, "_entity_managers", [])
    managers.append(manager)
    managers.append(balancer_manager)
    coordinator._entity_managers = managers
    result = async_add_entities(entities)
    if inspect.isawaitable(result):
        await result
