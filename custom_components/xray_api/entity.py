"""Shared Home Assistant entity helpers and stable ID generation."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .const import DOMAIN
from .coordinator import XrayCoordinator

try:
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity import Entity
except ImportError:  # pragma: no cover
    class Entity:  # type: ignore[no-redef]
        _attr_available = True

        def async_write_ha_state(self):
            return None

    DeviceInfo = dict  # type: ignore[assignment,misc]


def stable_unique_id(entry_id: str, entity_kind: str, tag: str | None = None) -> str:
    """Build a stable, collision-safe unique ID from original user input."""
    parts = [DOMAIN, str(entry_id), entity_kind]
    if tag is not None:
        parts.append(quote(str(tag), safe=""))
    return "_".join(parts)


def device_info(entry: Any, host: str, port: int) -> Any:
    """Return one device descriptor for a Config Entry."""
    identifier = (DOMAIN, str(getattr(entry, "entry_id", "unknown")))
    values = {
        "identifiers": {identifier},
        "name": f"Xray API ({host}:{port})",
        "manufacturer": "Xray",
        "model": "Xray gRPC API",
        "configuration_url": f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}",
    }
    try:
        return DeviceInfo(**values)
    except TypeError:
        return values


class XrayEntity(Entity):
    """Base class that subscribes to coordinator updates."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XrayCoordinator, entry: Any, kind: str, tag: str | None = None):
        self.coordinator = coordinator
        self.entry = entry
        self.kind = kind
        self.tag = tag
        self._attr_unique_id = stable_unique_id(entry.entry_id, kind, tag)
        self._attr_device_info = device_info(entry, coordinator.host, coordinator.port)
        self._unsub = coordinator.async_add_listener(self._handle_coordinator_update)

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        return None

    def _handle_coordinator_update(self) -> None:
        writer = getattr(self, "async_write_ha_state", None)
        if writer is not None:
            writer()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def outbound_attributes(status: Any) -> dict[str, Any]:
    """Build diagnostic attributes without leaking endpoint details."""
    values = {
        "outbound_tag": status.outbound_tag,
        "last_error_reason": status.last_error_reason,
        "last_seen_time": _iso(status.last_seen_time),
        "last_try_time": _iso(status.last_try_time),
        "last_seen_time_raw": status.last_seen_time_raw,
        "last_try_time_raw": status.last_try_time_raw,
        "delay_unit": "ms",
    }
    if status.health_ping is not None:
        values["health_ping"] = {
            "all": status.health_ping.all,
            "fail": status.health_ping.fail,
            "deviation": status.health_ping.deviation,
            "average": status.health_ping.average,
            "max": status.health_ping.max,
            "min": status.health_ping.min,
            "unit": "ms",
        }
    return values


def safe_raw_attributes(value: Any) -> dict[str, Any]:
    """Keep raw diagnostic mappings JSON-compatible and bounded."""
    if isinstance(value, Mapping):
        value = dict(value)
    try:
        json.dumps(value)
        return dict(value) if isinstance(value, Mapping) else {"value": value}
    except (TypeError, ValueError):
        return {"value": str(value)}
