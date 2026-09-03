"""Xray availability and outbound alive binary sensors."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from .coordinator import XrayCoordinator
from .entity import XrayEntity, outbound_attributes

try:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
except ImportError:  # pragma: no cover
    from .entity import Entity as _BaseEntity

    class BinarySensorEntity(_BaseEntity):  # type: ignore[no-redef]
        pass

    class BinarySensorDeviceClass:
        CONNECTIVITY = "connectivity"


class XrayBinarySensor(XrayEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, kind, *, tag=None, name=None):
        XrayEntity.__init__(self, coordinator, entry, kind, tag)
        self._attr_name = name or kind.replace("_", " ").title()


class ApiAvailableBinarySensor(XrayBinarySensor):
    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "api_available", name="API available")
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool:
        return self.coordinator.snapshot.api_available

    @property
    def available(self) -> bool:
        return True


class OutboundAliveBinarySensor(XrayBinarySensor):
    def __init__(self, coordinator, entry, tag):
        super().__init__(coordinator, entry, "outbound_alive", tag=tag, name=f"{tag} alive")

    @property
    def available(self) -> bool:
        return self.coordinator.outbound_available(self.tag)

    @property
    def is_on(self) -> bool | None:
        value = self.coordinator.snapshot.outbounds.get(self.tag)
        return value.alive if value is not None and self.available else None

    @property
    def extra_state_attributes(self):
        status = self.coordinator.snapshot.outbounds.get(self.tag)
        return outbound_attributes(status) if status is not None else {"outbound_tag": self.tag}


class _DynamicOutboundAlive:
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
                OutboundAliveBinarySensor(self.coordinator, self.entry, tag)
                for tag in sorted(new_tags)
            ])

    def async_shutdown(self):
        if self.unsub is not None:
            self.unsub()
            self.unsub = None


async def async_setup_entry(hass: Any, entry: Any, async_add_entities: Callable):
    coordinator: XrayCoordinator = hass.data["xray_api"][entry.entry_id]
    entities: list[Any] = [ApiAvailableBinarySensor(coordinator, entry)]
    manager = _DynamicOutboundAlive(coordinator, entry, async_add_entities)
    manager.seen.update(coordinator.known_outbound_tags)
    entities.extend(
        OutboundAliveBinarySensor(coordinator, entry, tag)
        for tag in sorted(coordinator.known_outbound_tags)
    )
    managers = getattr(coordinator, "_entity_managers", [])
    managers.append(manager)
    coordinator._entity_managers = managers
    result = async_add_entities(entities)
    if inspect.isawaitable(result):
        await result
