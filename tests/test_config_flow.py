from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from custom_components.xray_api import _async_options_updated
from custom_components.xray_api.api import (
    XrayConnectionError,
    XrayTimeoutError,
    XrayUnimplementedError,
)
from custom_components.xray_api.config_flow import (
    XrayApiConfigFlow,
    XrayApiOptionsFlow,
    endpoint_key,
    flow_error,
    normalize_balancer_tags,
    normalize_endpoint,
)
from custom_components.xray_api.const import DEFAULT_HOST, DEFAULT_PORT
from custom_components.xray_api.coordinator import UpdateFailed


def test_tags_accept_commas_and_lines_without_installation_defaults() -> None:
    assert normalize_balancer_tags(" fallback, priority\nfallback ") == ("fallback", "priority")
    assert normalize_balancer_tags(None) == ()


def test_endpoint_normalization_and_duplicate_key() -> None:
    assert normalize_endpoint(" [Xray.Local] ", "10085") == ("Xray.Local", 10085)
    assert endpoint_key("Xray.Local", 10085) == endpoint_key("xray.local", 10085)
    assert endpoint_key("2001:db8::1", 10085) == endpoint_key("2001:0db8:0:0:0:0:0:1", 10085)


def test_endpoint_validation_errors_are_actionable() -> None:
    with pytest.raises(ValueError, match="host_required"):
        normalize_endpoint("", 10085)
    with pytest.raises(ValueError, match="invalid_port"):
        normalize_endpoint("xray.local", 0)
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


def test_options_flow_does_not_assign_read_only_config_entry(monkeypatch) -> None:
    entry = SimpleNamespace(data={"host": "xray.local", "port": 10085}, options={})
    readonly_entry = property(lambda _flow: entry)
    monkeypatch.setattr(XrayApiOptionsFlow, "config_entry", readonly_entry, raising=False)

    flow = XrayApiOptionsFlow(entry)

    assert flow._entry is entry


def test_options_flow_factory_keeps_entry_for_compatibility() -> None:
    entry = SimpleNamespace(data={"host": "xray.local", "port": 10085}, options={})

    flow = XrayApiConfigFlow.async_get_options_flow(entry)

    assert flow._entry is entry


def test_options_flow_factory_uses_home_assistant_entry_property(monkeypatch) -> None:
    import custom_components.xray_api.config_flow as config_flow

    entry = SimpleNamespace(data={"host": "xray.local", "port": 10085}, options={})
    managed_base = type(
        "ManagedOptionsFlow",
        (),
        {"config_entry": property(lambda _flow: entry)},
    )
    monkeypatch.setattr(config_flow, "OptionsFlow", managed_base)
    monkeypatch.setattr(
        XrayApiOptionsFlow,
        "config_entry",
        property(lambda _flow: entry),
        raising=False,
    )

    flow = config_flow._new_options_flow(entry)

    assert flow._config_entry_override is None
    assert flow._entry is entry


def test_config_flow_discovers_outbounds_and_persists_selection(monkeypatch) -> None:
    calls = []

    async def discover(host, port, tags):
        calls.append((host, port, tuple(tags)))
        return ("direct", "proxy")

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    flow = XrayApiConfigFlow()

    result = asyncio.run(flow.async_step_user({"balancer_tags": "fallback"}))

    assert result["type"] == "form"
    assert result["step_id"] == "outbounds"
    assert calls == [(DEFAULT_HOST, DEFAULT_PORT, ("fallback",))]

    created = asyncio.run(
        flow.async_step_outbounds({"monitored_outbound_tags": ["proxy"]})
    )

    assert created["type"] == "create_entry"
    assert created["data"] == {"host": DEFAULT_HOST, "port": DEFAULT_PORT}
    assert created["options"] == {
        "balancer_tags": ("fallback",),
        "monitored_outbound_tags": ("proxy",),
    }


def test_config_flow_rejects_duplicate_endpoint_before_discovery(monkeypatch) -> None:
    discover_called = False

    async def discover(*args, **kwargs):
        nonlocal discover_called
        discover_called = True
        return ()

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "XRAY.LOCAL", "port": 10085},
        options={},
    )
    flow = XrayApiConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda _domain: [entry])
    )

    result = asyncio.run(
        flow.async_step_user({"host": "xray.local", "port": 10085, "balancer_tags": ""})
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "already_configured"}
    assert discover_called is False


def test_config_flow_keeps_endpoint_step_on_validation_errors(monkeypatch) -> None:
    async def discover(*args, **kwargs):
        raise XrayTimeoutError()

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    flow = XrayApiConfigFlow()

    result = asyncio.run(
        flow.async_step_user({"host": "xray.local", "port": 10085, "balancer_tags": ""})
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "timeout"}


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (XrayConnectionError(), "cannot_connect"),
        (XrayUnimplementedError(), "unimplemented"),
        (RuntimeError("boom"), "unknown"),
    ],
)
def test_config_flow_maps_all_validation_failures(monkeypatch, failure, expected) -> None:
    async def discover(*args, **kwargs):
        raise failure

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    flow = XrayApiConfigFlow()

    result = asyncio.run(
        flow.async_step_user({"host": "xray.local", "port": 10085, "balancer_tags": ""})
    )

    assert result["step_id"] == "user"
    assert result["errors"] == {"base": expected}


def test_reconfigure_validation_failure_leaves_entry_unchanged(monkeypatch) -> None:
    async def discover(*args, **kwargs):
        raise XrayConnectionError()

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    original = {"host": "old.local", "port": 10085}
    entry = SimpleNamespace(entry_id="entry-1", data=dict(original), options={})
    updates: list[tuple[tuple[object, ...], dict[str, object]]] = []
    flow = XrayApiConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda _domain: [entry],
            async_update_entry=lambda *args, **kwargs: updates.append((args, kwargs)),
        )
    )
    flow._reconfigure_entry_override = entry

    result = asyncio.run(
        flow.async_step_reconfigure(
            {"host": "new.local", "port": 10086, "balancer_tags": ""}
        )
    )

    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data == original
    assert updates == []


def test_options_flow_drops_unknown_outbounds_when_none_are_discovered() -> None:
    entry = SimpleNamespace(
        data={"host": "xray.local", "port": 10085},
        options={},
    )
    flow = XrayApiOptionsFlow(entry)

    result = asyncio.run(
        flow.async_step_init({"balancer_tags": "", "monitored_outbound_tags": ["ghost"]})
    )

    assert result["data"]["monitored_outbound_tags"] == ()


def test_reconfigure_flow_prefills_and_updates_entry_in_place(monkeypatch) -> None:
    async def discover(*args, **kwargs):
        return ("direct", "proxy")

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    updates = []
    reloads = []
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "old.local", "port": 10085, "outbound_tags": ["direct", "old"]},
        options={"balancer_tags": ("fallback",), "monitored_outbound_tags": ("direct",)},
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda _domain: [entry],
            async_update_entry=lambda *args, **kwargs: updates.append((args, kwargs)),
            async_schedule_reload=lambda entry_id: reloads.append(entry_id),
        )
    )
    flow = XrayApiConfigFlow()
    flow.hass = hass
    flow._reconfigure_entry_override = entry

    first = asyncio.run(flow.async_step_reconfigure(None))
    assert first["type"] == "form"
    assert first["step_id"] == "reconfigure"

    second = asyncio.run(
        flow.async_step_reconfigure(
            {"host": "new.local", "port": "10086", "balancer_tags": "fallback"}
        )
    )
    assert second["step_id"] == "outbounds"
    assert flow._wizard_selected_outbounds == ("direct",)

    result = asyncio.run(
        flow.async_step_outbounds({"monitored_outbound_tags": ["proxy"]})
    )

    assert result["type"] == "abort"
    assert updates[0][1]["data"] == {
        "host": "new.local",
        "port": 10086,
        "outbound_tags": ["direct", "old"],
    }
    assert updates[0][1]["options"] == {
        "balancer_tags": ("fallback",),
        "monitored_outbound_tags": ("proxy",),
    }
    assert updates[0][1]["title"] == "Xray API (new.local:10086)"
    assert reloads == ["entry-1"]


def test_reconfigure_flow_uses_native_update_and_abort_without_direct_reload(
    monkeypatch,
) -> None:
    async def discover(*args, **kwargs):
        return ("proxy",)

    monkeypatch.setattr(
        "custom_components.xray_api.config_flow.discover_endpoint", discover
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "old.local", "port": 10085},
        options={},
    )
    calls = []
    reloads = []
    flow = XrayApiConfigFlow()
    flow.hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_entries=lambda _domain: [entry],
            async_schedule_reload=lambda entry_id: reloads.append(entry_id),
        )
    )
    flow._reconfigure_entry_override = entry
    flow.async_update_and_abort = lambda *args, **kwargs: calls.append((args, kwargs)) or {
        "type": "abort",
        "reason": "reconfigure_successful",
    }

    asyncio.run(
        flow.async_step_reconfigure(
            {"host": "new.local", "port": 10086, "balancer_tags": "fallback"}
        )
    )
    result = asyncio.run(flow.async_step_outbounds({"monitored_outbound_tags": ["proxy"]}))

    assert result["type"] == "abort"
    assert calls[0][0] == (entry,)
    assert calls[0][1]["data"]["host"] == "new.local"
    assert calls[0][1]["options"]["monitored_outbound_tags"] == ("proxy",)
    assert reloads == []


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
    assert result["data"] == {
        "balancer_tags": ("fallback",),
        "monitored_outbound_tags": (),
    }


def test_options_flow_persists_selected_outbounds() -> None:
    entry = SimpleNamespace(
        data={"host": "xray.local", "port": 10085, "outbound_tags": ["direct", "proxy"]},
        options={
            "balancer_tags": ("fallback",),
            "monitored_outbound_tags": ("direct",),
            "future_option": "keep",
        },
    )
    flow = XrayApiOptionsFlow(entry)

    form = asyncio.run(flow.async_step_init())
    assert form["type"] == "form"

    result = asyncio.run(
        flow.async_step_init(
            {
                "balancer_tags": "fallback",
                "monitored_outbound_tags": ["proxy"],
            }
        )
    )

    assert result["data"] == {
        "balancer_tags": ("fallback",),
        "monitored_outbound_tags": ("proxy",),
        "future_option": "keep",
    }


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


def test_options_update_reconciles_dynamic_entities_before_failed_refresh() -> None:
    class Coordinator:
        host = "xray.local"
        port = 10085
        balancer_tags = ()

        def __init__(self):
            self.monitored = None
            self.updated = 0
            self._entity_managers = [self]

        def set_monitored_outbound_tags(self, tags):
            self.monitored = tags

        def _update(self):
            self.updated += 1

        async def async_refresh(self):
            raise UpdateFailed("endpoint unavailable")

    coordinator = Coordinator()
    hass = SimpleNamespace(data={"xray_api": {"entry-1": coordinator}})
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "xray.local", "port": 10085},
        options={"balancer_tags": (), "monitored_outbound_tags": ("proxy",)},
    )

    asyncio.run(_async_options_updated(hass, entry))

    assert coordinator.monitored == ("proxy",)
    assert coordinator.updated == 1


def test_options_update_schedules_reload_when_reconfigure_changes_endpoint() -> None:
    class Coordinator:
        host = "old.local"
        port = 10085

        def __init__(self):
            self.refresh_called = False

        async def async_refresh(self):
            self.refresh_called = True

    coordinator = Coordinator()
    reloads: list[str] = []
    hass = SimpleNamespace(
        data={"xray_api": {"entry-1": coordinator}},
        config_entries=SimpleNamespace(
            async_schedule_reload=lambda entry_id: reloads.append(entry_id)
        ),
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        data={"host": "new.local", "port": 10086},
        options={"balancer_tags": (), "monitored_outbound_tags": ()},
    )

    asyncio.run(_async_options_updated(hass, entry))

    assert reloads == ["entry-1"]
    assert coordinator.refresh_called is False
