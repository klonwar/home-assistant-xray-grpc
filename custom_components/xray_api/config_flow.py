"""Config and Options flows for Xray endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Iterable, Mapping
from typing import Any

from .api import (
    GrpcXrayApi,
    XrayConnectionError,
    XrayTimeoutError,
    XrayUnimplementedError,
)
from .const import (
    CONF_BALANCER_TAGS,
    CONF_HOST,
    CONF_MONITORED_OUTBOUND_TAGS,
    CONF_OUTBOUND_TAGS,
    CONF_PORT,
    CONFIG_FLOW_TIMEOUT,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
)

try:
    import voluptuous as vol
except ImportError:  # pragma: no cover
    vol = None

try:
    from homeassistant import config_entries
    from homeassistant.config_entries import ConfigFlow, OptionsFlow
except ImportError:  # pragma: no cover
    config_entries = None

    class ConfigFlow:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            self.hass = kwargs.get("hass")

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}

try:
    from homeassistant.helpers.selector import (
        SelectSelector,
        SelectSelectorConfig,
        SelectSelectorMode,
    )
except ImportError:  # pragma: no cover - selectors are only available in HA.
    SelectSelector = None
    SelectSelectorConfig = None
    SelectSelectorMode = None

    class OptionsFlow:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            self.hass = kwargs.get("hass")

        def async_show_form(self, **kwargs):
            return {"type": "form", **kwargs}

        def async_create_entry(self, **kwargs):
            return {"type": "create_entry", **kwargs}


def normalize_balancer_tags(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize comma/newline-separated balancer tags without defaults."""
    if value is None:
        return ()
    values = [value] if isinstance(value, str) else value
    tags: list[str] = []
    for item in values:
        for token in str(item).replace(",", "\n").splitlines():
            token = token.strip()
            if token and token not in tags:
                tags.append(token)
    return tuple(tags)


def normalize_endpoint(host: str, port: int | str | None) -> tuple[str, int]:
    """Normalize endpoint values for duplicate detection and channel targets."""
    clean_host = str(host or "").strip()
    if not clean_host:
        raise ValueError("host_required")
    if clean_host.startswith("[") and clean_host.endswith("]"):
        clean_host = clean_host[1:-1]
    try:
        clean_port = int(port if port not in (None, "") else DEFAULT_PORT)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid_port") from error
    if not 1 <= clean_port <= 65535:
        raise ValueError("invalid_port")
    return clean_host, clean_port


def endpoint_key(host: str, port: int | str | None) -> str:
    clean_host, clean_port = normalize_endpoint(host, port)
    try:
        identity_host = ipaddress.ip_address(clean_host).compressed
    except ValueError:
        identity_host = clean_host.casefold()
    return f"{identity_host.casefold()}:{clean_port}"


async def discover_endpoint(
    host: str,
    port: int,
    balancer_tags: Iterable[str] = (),
    *,
    api_factory: Any = GrpcXrayApi,
    timeout: float = CONFIG_FLOW_TIMEOUT,
) -> tuple[str, ...]:
    """Validate an endpoint and return outbound tags from Observatory."""
    api = api_factory(host, port)
    try:
        observatory, _stats = await asyncio.wait_for(
            asyncio.gather(api.async_get_observatory(timeout), api.async_get_stats(timeout)),
            timeout=timeout,
        )
        tags = normalize_balancer_tags(balancer_tags)
        if tags:
            await asyncio.wait_for(
                asyncio.gather(*(api.async_get_balancer(tag, timeout) for tag in tags)),
                timeout=timeout,
            )
        if isinstance(observatory, Mapping):
            return tuple(str(tag) for tag in observatory if str(tag))
        return ()
    finally:
        close = getattr(api, "async_close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


async def validate_endpoint(
    host: str,
    port: int,
    balancer_tags: Iterable[str] = (),
    *,
    api_factory: Any = GrpcXrayApi,
    timeout: float = CONFIG_FLOW_TIMEOUT,
) -> None:
    """Validate required service support with a bounded gRPC deadline."""
    await discover_endpoint(
        host,
        port,
        balancer_tags,
        api_factory=api_factory,
        timeout=timeout,
    )


async def validate_routing_support(
    host: str,
    port: int,
    balancer_tags: Iterable[str],
    *,
    api_factory: Any = GrpcXrayApi,
    timeout: float = CONFIG_FLOW_TIMEOUT,
) -> None:
    """Check Routing support for configured tags with a bounded deadline."""
    tags = normalize_balancer_tags(balancer_tags)
    if not tags:
        return
    api = api_factory(host, port)
    try:
        await asyncio.wait_for(
            asyncio.gather(*(api.async_get_balancer(tag, timeout) for tag in tags)),
            timeout=timeout,
        )
    finally:
        close = getattr(api, "async_close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


def flow_error(error: BaseException) -> str:
    """Map normalized API exceptions to translation keys."""
    if isinstance(error, XrayTimeoutError) or isinstance(
        error, (asyncio.TimeoutError, TimeoutError)
    ):
        return "timeout"
    if isinstance(error, XrayUnimplementedError):
        return "unimplemented"
    if isinstance(error, XrayConnectionError) or isinstance(error, ConnectionError):
        return "cannot_connect"
    return "unknown"


def _entry_option(entry: Any, key: str, default: Any = ()) -> Any:
    """Read an option, falling back to legacy Config Entry data."""
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    return options.get(key, data.get(key, default))


def _configured_balancer_tags(entry: Any) -> tuple[str, ...]:
    return normalize_balancer_tags(_entry_option(entry, CONF_BALANCER_TAGS))


def _configured_outbound_tags(entry: Any, available: Iterable[str]) -> tuple[str, ...]:
    available_set = set(str(tag) for tag in available if str(tag))
    marker = object()
    value = (getattr(entry, "options", {}) or {}).get(
        CONF_MONITORED_OUTBOUND_TAGS,
        marker,
    )
    if value is marker:
        value = (getattr(entry, "data", {}) or {}).get(CONF_OUTBOUND_TAGS, ())
    return tuple(tag for tag in normalize_balancer_tags(value) if tag in available_set)


def _endpoint_schema(entry: Any | None = None):
    host = str(_entry_option(entry, CONF_HOST, DEFAULT_HOST) if entry is not None else DEFAULT_HOST)
    port = _entry_option(entry, CONF_PORT, DEFAULT_PORT) if entry is not None else DEFAULT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    balancers = _configured_balancer_tags(entry) if entry is not None else ()
    current_value = "\n".join(balancers)
    if vol is None:
        return {CONF_HOST: str, CONF_PORT: int, CONF_BALANCER_TAGS: str}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host): str,
            vol.Required(CONF_PORT, default=port): vol.Coerce(int),
            vol.Optional(CONF_BALANCER_TAGS, default=current_value): str,
        }
    )


def _form_schema():
    """Return the initial endpoint form schema for compatibility callers."""
    return _endpoint_schema()


def _outbound_schema(options: Iterable[str], selected: Iterable[str]):
    options = tuple(dict.fromkeys(str(tag) for tag in options if str(tag)))
    selected = tuple(tag for tag in selected if tag in options)
    if vol is None or SelectSelector is None or SelectSelectorConfig is None:
        return {CONF_MONITORED_OUTBOUND_TAGS: list}
    selector_kwargs: dict[str, Any] = {
        "options": list(options),
        "multiple": True,
    }
    if SelectSelectorMode is not None:
        selector_kwargs["mode"] = SelectSelectorMode.LIST
    else:  # pragma: no cover - old HA selector API.
        selector_kwargs["mode"] = "list"
    selector = SelectSelector(SelectSelectorConfig(**selector_kwargs))
    return vol.Schema(
        {
            vol.Required(CONF_MONITORED_OUTBOUND_TAGS, default=list(selected)): selector,
        }
    )


def _schedule_reload(hass: Any, entry: Any) -> None:
    manager = getattr(hass, "config_entries", None)
    schedule = getattr(manager, "async_schedule_reload", None)
    if schedule is not None:
        schedule(entry.entry_id)


class _XraySetupFlowMixin:
    """Shared endpoint/discovery/selection steps for add and Reconfigure."""

    def _existing_keys(self, exclude_entry: Any | None = None) -> set[str]:
        entries = getattr(getattr(self, "hass", None), "config_entries", None)
        if entries is None:
            return set()
        result: set[str] = set()
        for item in entries.async_entries(DOMAIN):
            if exclude_entry is not None and getattr(item, "entry_id", None) == getattr(
                exclude_entry, "entry_id", None
            ):
                continue
            host = item.data.get(CONF_HOST)
            if not host:
                continue
            try:
                result.add(endpoint_key(host, item.data.get(CONF_PORT, DEFAULT_PORT)))
            except ValueError:
                continue
        return result

    def _reconfigure_entry(self) -> Any | None:
        override = getattr(self, "_reconfigure_entry_override", None)
        if override is not None:
            return override
        getter = getattr(self, "_get_reconfigure_entry", None)
        return getter() if callable(getter) else None

    async def _async_step_endpoint(
        self,
        user_input: dict[str, Any] | None,
        *,
        entry: Any | None,
        step_id: str,
    ):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                host, port = normalize_endpoint(
                    user_input.get(CONF_HOST, DEFAULT_HOST),
                    user_input.get(CONF_PORT, DEFAULT_PORT),
                )
                tags = normalize_balancer_tags(user_input.get(CONF_BALANCER_TAGS))
                if endpoint_key(host, port) in self._existing_keys(exclude_entry=entry):
                    errors["base"] = "already_configured"
                else:
                    available = await discover_endpoint(host, port, tags)
                    self._wizard_host = host
                    self._wizard_port = port
                    self._wizard_balancer_tags = tags
                    self._wizard_available_outbounds = available
                    self._wizard_entry = entry
                    selected = _configured_outbound_tags(entry, available) if entry else ()
                    self._wizard_selected_outbounds = selected
                    return self.async_show_form(
                        step_id="outbounds",
                        data_schema=_outbound_schema(available, selected),
                        errors={},
                    )
            except ValueError as error:
                errors["base"] = str(error)
            except Exception as error:  # normalized API errors are safe to classify.
                errors["base"] = flow_error(error)
        return self.async_show_form(
            step_id=step_id,
            data_schema=_endpoint_schema(entry),
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        return await self._async_step_endpoint(user_input, entry=None, step_id="user")

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        entry = self._reconfigure_entry()
        return await self._async_step_endpoint(
            user_input,
            entry=entry,
            step_id="reconfigure",
        )

    async def async_step_outbounds(self, user_input: dict[str, Any] | None = None):
        available = getattr(self, "_wizard_available_outbounds", ())
        selected = getattr(self, "_wizard_selected_outbounds", ())
        if user_input is None:
            return self.async_show_form(
                step_id="outbounds",
                data_schema=_outbound_schema(available, selected),
                errors={},
            )
        chosen = tuple(
            tag
            for tag in normalize_balancer_tags(
                user_input.get(CONF_MONITORED_OUTBOUND_TAGS, ())
            )
            if tag in available
        )
        entry = getattr(self, "_wizard_entry", None)
        data = {CONF_HOST: self._wizard_host, CONF_PORT: self._wizard_port}
        options = {
            CONF_BALANCER_TAGS: self._wizard_balancer_tags,
            CONF_MONITORED_OUTBOUND_TAGS: chosen,
        }
        if entry is None:
            return self.async_create_entry(
                title=f"Xray API ({self._wizard_host}:{self._wizard_port})",
                data=data,
                options=options,
            )

        updated_data = dict(getattr(entry, "data", {}) or {})
        updated_data.pop(CONF_BALANCER_TAGS, None)
        updated_data.update(data)
        updated_options = dict(getattr(entry, "options", {}) or {})
        updated_options.update(options)
        title = f"Xray API ({self._wizard_host}:{self._wizard_port})"
        updater = getattr(self, "async_update_and_abort", None)
        if callable(updater):
            result = updater(
                entry,
                data=updated_data,
                options=updated_options,
                title=title,
                reason="reconfigure_successful",
            )
            return result
        manager = getattr(getattr(self, "hass", None), "config_entries", None)
        update_entry = getattr(manager, "async_update_entry", None)
        if update_entry is not None:
            update_entry(entry, data=updated_data, options=updated_options, title=title)
            _schedule_reload(self.hass, entry)
        abort = getattr(self, "async_abort", None)
        if callable(abort):
            return abort(reason="reconfigure_successful")
        return {"type": "abort", "reason": "reconfigure_successful"}


try:
    class XrayApiConfigFlow(_XraySetupFlowMixin, ConfigFlow, domain=DOMAIN):
        """Handle adding and reconfiguring an Xray endpoint."""

        VERSION = 1

        @staticmethod
        def async_get_options_flow(config_entry):
            return _new_options_flow(config_entry)
except TypeError:  # pragma: no cover - legacy HA ConfigFlow base.
    class XrayApiConfigFlow(_XraySetupFlowMixin, ConfigFlow):
        """Compatibility wrapper for legacy Home Assistant."""

        VERSION = 1

        @staticmethod
        def async_get_options_flow(config_entry):
            return _new_options_flow(config_entry)


class XrayApiOptionsFlow(OptionsFlow):
    """Edit balancer tags and the explicit outbound monitoring selection."""

    def __init__(self, config_entry: Any | None = None):
        """Initialize without assigning Home Assistant's read-only config_entry."""
        self._config_entry_override = config_entry

    @property
    def _entry(self):
        """Return the config entry supplied by HA or by a compatibility caller."""
        if self._config_entry_override is not None:
            return self._config_entry_override
        return self.config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        entry = self._entry
        known = tuple(
            str(tag)
            for tag in (getattr(entry, "data", {}) or {}).get(CONF_OUTBOUND_TAGS, ())
            if str(tag)
        )
        current_value = (getattr(entry, "options", {}) or {}).get(
            CONF_MONITORED_OUTBOUND_TAGS,
            known,
        )
        selected = normalize_balancer_tags(current_value)
        available = tuple(dict.fromkeys((*known, *selected)))
        if user_input is not None:
            tags = normalize_balancer_tags(user_input.get(CONF_BALANCER_TAGS))
            chosen = tuple(
                tag
                for tag in normalize_balancer_tags(
                    user_input.get(CONF_MONITORED_OUTBOUND_TAGS, ())
                )
                if tag in available
            )
            if tags:
                try:
                    await validate_routing_support(
                        entry.data.get(CONF_HOST, ""),
                        int(entry.data.get(CONF_PORT, DEFAULT_PORT)),
                        tags,
                    )
                except XrayUnimplementedError:
                    errors["base"] = "unimplemented"
                except (
                    XrayTimeoutError,
                    XrayConnectionError,
                    asyncio.TimeoutError,
                    TimeoutError,
                    ConnectionError,
                    OSError,
                ):
                    # Offline edits are accepted; the next coordinator refresh
                    # will expose Routing as unavailable until it recovers.
                    pass
                except Exception as error:  # pragma: no cover - defensive flow boundary.
                    errors["base"] = flow_error(error)
            if not errors:
                updated_options = dict(getattr(entry, "options", {}) or {})
                updated_options.update(
                    {
                        CONF_BALANCER_TAGS: tags,
                        CONF_MONITORED_OUTBOUND_TAGS: chosen,
                    }
                )
                return self.async_create_entry(
                    data=updated_options
                )
        current_balancers = _configured_balancer_tags(entry)
        current_balancer_value = "\n".join(current_balancers)
        schema = _outbound_schema(available, selected)
        if vol is not None and SelectSelector is not None and SelectSelectorConfig is not None:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_BALANCER_TAGS,
                        default=current_balancer_value,
                    ): str,
                    **schema.schema,
                }
            )
        else:
            schema = {
                CONF_BALANCER_TAGS: str,
                CONF_MONITORED_OUTBOUND_TAGS: list,
            }
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)


def _new_options_flow(config_entry):
    """Create an Options Flow without assigning HA's read-only entry property."""
    flow = XrayApiOptionsFlow()
    # Home Assistant now injects the entry into OptionsFlow and exposes it via
    # a read-only property. Older runtimes and our pure-Python fallback do not.
    if not hasattr(OptionsFlow, "config_entry"):
        flow._config_entry_override = config_entry
    return flow
