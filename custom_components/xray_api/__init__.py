"""Home Assistant integration for a remote Xray gRPC API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:  # Home Assistant imports are kept at the integration boundary.
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.exceptions import ConfigEntryNotReady
except ImportError:  # pragma: no cover - allows pure-Python test imports.
    class HomeAssistant:  # type: ignore[no-redef]
        """Fallback type used when Home Assistant is not installed."""

    class ConfigEntry:  # type: ignore[no-redef]
        """Fallback type used when Home Assistant is not installed."""

        entry_id = "test-entry"
        data: dict[str, Any] = {}
        options: dict[str, Any] = {}

    class ConfigEntryNotReady(Exception):  # type: ignore[no-redef]
        """Fallback setup error used by pure-Python tests."""

from .api import XrayApiError
from .const import (
    CONF_BALANCER_TAGS,
    CONF_HOST,
    CONF_OUTBOUND_TAGS,
    CONF_PORT,
    DEFAULT_PORT,
    DOMAIN,
)
from .coordinator import UpdateFailed, XrayCoordinator

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ("sensor", "binary_sensor")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an Xray endpoint from a config entry."""
    if not hasattr(hass, "data"):
        hass.data = {}
    domain_data = hass.data.setdefault(DOMAIN, {})
    host = entry.data[CONF_HOST]
    port = int(entry.data.get(CONF_PORT, DEFAULT_PORT))
    tags = entry.options.get(
        CONF_BALANCER_TAGS,
        entry.data.get(CONF_BALANCER_TAGS, ()),
    )
    known_outbound_tags = tuple(
        str(tag) for tag in entry.data.get(CONF_OUTBOUND_TAGS, ()) if str(tag)
    )

    def _persist_outbound_tags(updated_tags: tuple[str, ...]) -> None:
        current_data = dict(entry.data)
        if tuple(current_data.get(CONF_OUTBOUND_TAGS, ())) == updated_tags:
            return
        current_data[CONF_OUTBOUND_TAGS] = list(updated_tags)
        updater = getattr(getattr(hass, "config_entries", None), "async_update_entry", None)
        if updater is not None:
            updater(entry, data=current_data)
            return
        # Minimal test doubles may not provide ConfigEntries.async_update_entry.
        try:
            entry.data[CONF_OUTBOUND_TAGS] = list(updated_tags)
        except (AttributeError, TypeError):
            _LOGGER.debug("Could not persist discovered Xray outbound tags")

    coordinator = XrayCoordinator(
        host,
        port,
        balancer_tags=tags,
        hass=hass,
        known_outbound_tags=known_outbound_tags,
        on_outbound_tags=_persist_outbound_tags,
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except (
        ConfigEntryNotReady,
        UpdateFailed,
        XrayApiError,
        asyncio.TimeoutError,
        TimeoutError,
        ConnectionError,
        OSError,
    ):
        # The coordinator publishes an unavailable snapshot before raising;
        # keep the entry loaded so HA can expose unknown/offline entities and
        # the scheduled refresh can recover the endpoint.
        _LOGGER.warning("Xray API is unavailable during setup; entities will start unavailable")
    domain_data[entry.entry_id] = coordinator
    add_update_listener = getattr(entry, "add_update_listener", None)
    if add_update_listener is not None:
        coordinator._options_unsub = add_update_listener(_async_options_updated)

    # Platforms are forwarded by HA; minimal test stubs can omit this API.
    try:
        await _async_forward_entry_setups(hass, entry, PLATFORMS)
    except Exception:
        unload = getattr(getattr(hass, "config_entries", None), "async_unload_platforms", None)
        if unload is not None:
            try:
                result = unload(entry, list(PLATFORMS))
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                # Cleanup below is still required if HA cannot unload a partial setup.
                pass
        domain_data.pop(entry.entry_id, None)
        options_unsub = getattr(coordinator, "_options_unsub", None)
        if options_unsub is not None:
            options_unsub()
        await coordinator.async_shutdown()
        raise
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed balancer tags without replacing the shared API channel."""
    coordinator = getattr(hass, "data", {}).get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return
    coordinator.balancer_tags = tuple(
        dict.fromkeys(
            str(tag)
            for tag in entry.options.get(
                CONF_BALANCER_TAGS,
                entry.data.get(CONF_BALANCER_TAGS, ()),
            )
            if str(tag)
        )
    )
    try:
        await coordinator.async_refresh()
    except (
        UpdateFailed,
        XrayApiError,
        asyncio.TimeoutError,
        TimeoutError,
        ConnectionError,
        OSError,
    ):
        # Options are already persisted by HA. Keep the entry loaded and let
        # the coordinator publish unavailable/degraded state until recovery.
        return


async def _async_forward_entry_setups(
    hass: HomeAssistant, entry: ConfigEntry, platforms: tuple[str, ...]
) -> None:
    """Forward platforms using whichever HA API is available."""
    config_entries = getattr(hass, "config_entries", None)
    if config_entries is None:
        return
    forward = getattr(config_entries, "async_forward_entry_setups", None)
    if forward is not None:
        await forward(entry, list(platforms))
        return
    forward_one = getattr(config_entries, "async_forward_entry_setup", None)
    if forward_one is not None:
        for platform in platforms:
            await forward_one(entry, platform)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an Xray endpoint and close its shared gRPC channel."""
    domain_data = getattr(hass, "data", {}).get(DOMAIN, {})
    coordinator = domain_data.get(entry.entry_id)
    if coordinator is None:
        return True
    unload = getattr(getattr(hass, "config_entries", None), "async_unload_platforms", None)
    if unload is not None:
        unloaded = await unload(entry, list(PLATFORMS))
        if not unloaded:
            return False
    domain_data.pop(entry.entry_id, None)
    options_unsub = getattr(coordinator, "_options_unsub", None)
    if options_unsub is not None:
        options_unsub()
    await coordinator.async_shutdown()
    return True
