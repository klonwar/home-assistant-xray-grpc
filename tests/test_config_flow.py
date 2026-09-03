from __future__ import annotations

import asyncio
from types import SimpleNamespace

from custom_components.xray_api import _async_options_updated
from custom_components.xray_api.api import (
    XrayConnectionError,
    XrayTimeoutError,
    XrayUnimplementedError,
)
from custom_components.xray_api.config_flow import (
    XrayApiOptionsFlow,
    endpoint_key,
    flow_error,
    normalize_balancer_tags,
    normalize_endpoint,
)
from custom_components.xray_api.coordinator import UpdateFailed


def test_tags_accept_commas_and_lines_without_installation_defaults() -> None:
    assert normalize_balancer_tags(" fallback, priority\nfallback ") == ("fallback", "priority")
    assert normalize_balancer_tags(None) == ()


def test_endpoint_normalization_and_duplicate_key() -> None:
    assert normalize_endpoint(" [Xray.Local] ", "10085") == ("Xray.Local", 10085)
    assert endpoint_key("Xray.Local", 10085) == endpoint_key("xray.local", 10085)
    assert endpoint_key("2001:db8::1", 10085) == endpoint_key("2001:0db8:0:0:0:0:0:1", 10085)


def test_endpoint_validation_errors_are_actionable() -> None:
    try:
        normalize_endpoint("", 10085)
    except ValueError as error:
        assert str(error) == "host_required"
    try:
        normalize_endpoint("xray.local", 0)
    except ValueError as error:
        assert str(error) == "invalid_port"
    assert flow_error(XrayTimeoutError()) == "timeout"
    assert flow_error(XrayUnimplementedError()) == "unimplemented"


def test_options_flow_rejects_routing_unimplemented(monkeypatch) -> None:
    async def unavailable(*args, **kwargs):
        raise XrayUnimplementedError()

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.validate_routing_support", unavailable
    )
    entry = SimpleNamespace(data={"host": "xray.local", "port": 10085}, options={})
    flow = XrayApiOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init({"balancer_tags": "fallback"}))

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unimplemented"


def test_options_flow_accepts_offline_routing_edit(monkeypatch) -> None:
    async def offline(*args, **kwargs):
        raise XrayConnectionError()

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.validate_routing_support", offline
    )
    entry = SimpleNamespace(data={"host": "xray.local", "port": 10085}, options={})
    flow = XrayApiOptionsFlow(entry)

    result = asyncio.run(flow.async_step_init({"balancer_tags": "fallback"}))

    assert result["type"] == "create_entry"
    assert result["data"] == {"balancer_tags": ("fallback",)}


def test_options_update_keeps_persisted_tags_when_refresh_is_unavailable() -> None:
    class Coordinator:
        balancer_tags = ()

        async def async_refresh(self):
            raise UpdateFailed("endpoint unavailable")

    coordinator = Coordinator()
    hass = SimpleNamespace(data={"xray_api": {"entry-1": coordinator}})
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "xray.local", "port": 10085},
        options={"balancer_tags": ("fallback",)},
    )

    asyncio.run(_async_options_updated(hass, entry))

    assert coordinator.balancer_tags == ("fallback",)
