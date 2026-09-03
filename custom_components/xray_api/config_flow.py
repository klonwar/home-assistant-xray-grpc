"""Config and Options flows for Xray endpoints."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Iterable
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
    CONF_PORT,
    CONFIG_FLOW_TIMEOUT,
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


async def validate_endpoint(
    host: str,
    port: int,
    balancer_tags: Iterable[str] = (),
    *,
    api_factory: Any = GrpcXrayApi,
    timeout: float = CONFIG_FLOW_TIMEOUT,
) -> None:
    """Validate required service support with a bounded gRPC deadline."""
    api = api_factory(host, port)
    try:
        await asyncio.wait_for(
            asyncio.gather(api.async_get_observatory(timeout), api.async_get_stats(timeout)),
            timeout=timeout,
        )
        tags = normalize_balancer_tags(balancer_tags)
        if tags:
            await asyncio.wait_for(api.async_get_balancer(tags[0], timeout), timeout=timeout)
    finally:
        close = getattr(api, "async_close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


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
        await asyncio.wait_for(api.async_get_balancer(tags[0], timeout), timeout=timeout)
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


def _form_schema():
    if vol is None:
        return {CONF_HOST: str, CONF_PORT: int, CONF_BALANCER_TAGS: str}
    return vol.Schema(
        {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
            vol.Optional(CONF_BALANCER_TAGS, default=""): str,
        }
    )


try:
    class XrayApiConfigFlow(ConfigFlow, domain=DOMAIN):
        """Handle adding an Xray endpoint."""

        VERSION = 1

        async def async_step_user(self, user_input: dict[str, Any] | None = None):
            errors: dict[str, str] = {}
            if user_input is not None:
                try:
                    host, port = normalize_endpoint(
                        user_input.get(CONF_HOST, ""), user_input.get(CONF_PORT, DEFAULT_PORT)
                    )
                    tags = normalize_balancer_tags(user_input.get(CONF_BALANCER_TAGS))
                    existing = self._existing_keys()
                    if endpoint_key(host, port) in existing:
                        errors["base"] = "already_configured"
                    else:
                        await validate_endpoint(host, port, tags)
                        return self.async_create_entry(
                            title=f"Xray API ({host}:{port})",
                            data={CONF_HOST: host, CONF_PORT: port, CONF_BALANCER_TAGS: tags},
                        )
                except ValueError as error:
                    errors["base"] = str(error)
                except Exception as error:  # normalized API errors are safe to classify.
                    errors["base"] = flow_error(error)
            return self.async_show_form(
                step_id="user",
                data_schema=_form_schema(),
                errors=errors,
            )

        def _existing_keys(self) -> set[str]:
            entries = getattr(getattr(self, "hass", None), "config_entries", None)
            if entries is None:
                return set()
            current = entries.async_entries(DOMAIN)
            return {
                endpoint_key(item.data.get(CONF_HOST, ""), item.data.get(CONF_PORT, DEFAULT_PORT))
                for item in current
                if item.data.get(CONF_HOST)
            }

        @staticmethod
        def async_get_options_flow(config_entry):
            return XrayApiOptionsFlow(config_entry)
except TypeError:  # pragma: no cover - legacy HA ConfigFlow base.
    class XrayApiConfigFlow(ConfigFlow):
        """Compatibility wrapper for legacy Home Assistant."""

        VERSION = 1

        async def async_step_user(self, user_input=None):
            return {"type": "form", "step_id": "user", "data_schema": _form_schema(), "errors": {}}

        @staticmethod
        def async_get_options_flow(config_entry):
            return XrayApiOptionsFlow(config_entry)


class XrayApiOptionsFlow(OptionsFlow):
    """Edit the optional user-supplied balancer tags."""

    def __init__(self, config_entry):
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            tags = normalize_balancer_tags(user_input.get(CONF_BALANCER_TAGS))
            if tags:
                try:
                    await validate_routing_support(
                        self.config_entry.data.get(CONF_HOST, ""),
                        int(self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)),
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
                return self.async_create_entry(data={CONF_BALANCER_TAGS: tags})
        current = self.config_entry.options.get(
            CONF_BALANCER_TAGS,
            self.config_entry.data.get(CONF_BALANCER_TAGS, ()),
        )
        current_value = "\n".join(current) if not isinstance(current, str) else current
        schema = (
            vol.Schema({vol.Optional(CONF_BALANCER_TAGS, default=current_value): str})
            if vol is not None
            else {CONF_BALANCER_TAGS: str}
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
