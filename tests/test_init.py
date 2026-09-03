from __future__ import annotations

import asyncio
from types import SimpleNamespace

import custom_components.xray_api as integration
from custom_components.xray_api.coordinator import UpdateFailed


def test_setup_keeps_entry_loaded_when_initial_endpoint_is_down(monkeypatch) -> None:
    class OfflineCoordinator:
        def __init__(self, *args, **kwargs):
            self.shutdown_called = False

        async def async_config_entry_first_refresh(self):
            raise UpdateFailed("endpoint unavailable")

        async def async_shutdown(self):
            self.shutdown_called = True

    forwarded: list[tuple[str, ...]] = []

    async def forward(_entry, platforms):
        forwarded.append(tuple(platforms))

    monkeypatch.setattr(integration, "XrayCoordinator", OfflineCoordinator)
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "xray.local", "port": 10085, "outbound_tags": ["vm9"]},
        options={},
    )
    hass = SimpleNamespace(
        data={},
        config_entries=SimpleNamespace(async_forward_entry_setups=forward),
    )

    result = asyncio.run(integration.async_setup_entry(hass, entry))

    assert result is True
    assert tuple(hass.data["xray_api"]) == ("entry-1",)
    assert forwarded == [("sensor", "binary_sensor")]
