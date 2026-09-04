# Home Assistant Xray API integration: implementation handoff

## Status

Design validated with the user. This document is a handoff for implementation in a separate public repository. It is intentionally independent of this Home Assistant configuration repository.

## Goal

Create a HACS-compatible Home Assistant custom integration that monitors a remote Xray instance, including Xray instances managed by Xkeen on Keenetic.

The integration must help answer, at a given moment:

- Is the Xray API/core available?
- Is the system online, degraded, or offline?
- Which observed outbound is alive or dead?
- What delay and last error did Observatory report?
- Which candidate did each configured balancer select?
- How much traffic passed through each outbound?

The integration monitors Xray core. Xkeen is a supported deployment scenario, not the API contract or a runtime dependency. Xray's API is gRPC-based and exposes the Observatory, Stats, and Routing services used below.

## Research conclusion

Existing projects such as [xkeen-panel](https://github.com/Dearonski/xkeen-panel) and [xkeen-ui](https://github.com/fan92rus/xkeen-ui) are standalone web panels. The [HA Xray add-on](https://home-assistant-apps.j0rsa.com/apps/xray/) runs Xray inside Home Assistant rather than monitoring a remote Xkeen instance. A native read-only custom integration is the best fit.

## V1 scope

Include:

- one Config Entry per Xray endpoint;
- insecure gRPC over a trusted LAN/VPN;
- `DataUpdateCoordinator` polling approximately every 15 seconds;
- Observatory outbound status;
- Xray uptime;
- cumulative uplink/downlink traffic per outbound;
- balancer target information for user-supplied balancer tags;
- standard HA entities and diagnostics attributes;
- English and Russian translations if practical.

Do not include:

- Bubble Card, popup, dashboard YAML, or any project-specific Lovelace UI;
- Xray/Xkeen log ingestion or log parsing;
- SSH/SFTP, shell commands, or config file reads;
- notifications or automations;
- mutating Xray RPCs;
- per-request route tracing;
- automatic enumeration of balancer definitions;
- TLS/authentication unless implemented as a complete, tested feature.

History retention is Home Assistant Recorder's responsibility. The integration only needs correct state classes so users can chart at least two days of data in their own HA instance.

## Xray API contract

### Observatory

Call:

```text
ObservatoryService.GetOutboundStatus
```

Use the official [Observatory command proto](https://raw.githubusercontent.com/XTLS/Xray-core/main/app/observatory/command/command.proto) and [Observatory data model](https://raw.githubusercontent.com/XTLS/Xray-core/main/app/observatory/config.proto).

For every returned outbound, preserve:

- `outbound_tag`;
- `alive`;
- `delay`;
- `last_error_reason`;
- `last_seen_time`;
- `last_try_time`;
- optional health-ping measurements (`all`, `fail`, `deviation`, `average`, `max`, `min`).

Do not classify or rewrite `last_error_reason`; Xray marks it as not machine-readable. Preserve the raw value in an attribute. Validate delay and health-ping units against the pinned proto/source and test fixtures before publishing.

Only outbounds returned by Observatory are discoverable. An arbitrary outbound not included in `subjectSelector` will not appear as healthy merely because it exists in Xray configuration.

### Stats

Call:

```text
StatsService.GetSysStats
StatsService.QueryStats
```

References: [Stats command proto](https://raw.githubusercontent.com/XTLS/Xray-core/main/app/stats/command/command.proto) and [Xray statistics documentation](https://xtls.github.io/en/config/stats.html).

Use `GetSysStats` for Xray uptime in seconds.

Use `QueryStats` with `reset=false`. Query outbound counters using the `outbound>>>` prefix and map:

```text
outbound>>>[tag]>>>traffic>>>uplink
outbound>>>[tag]>>>traffic>>>downlink
```

Counters are bytes and must be exposed as `state_class: total_increasing`. A missing counter is `unavailable`, not zero. Document and test behavior after an Xray restart/counter reset.

### Routing

Call:

```text
RoutingService.GetBalancerInfo
```

Reference: [Routing command proto](https://raw.githubusercontent.com/XTLS/Xray-core/main/app/router/command/command.proto).

The API accepts a balancer tag but does not provide a reliable list of balancer definitions. The Config Flow therefore accepts balancer tags as user input. Do not duplicate selectors, strategies, costs, or fallback configuration in the integration.

Expose the returned `principle_target` and `override` target. Label `principle_target` as a strategy-selected candidate, not as the guaranteed route of every individual connection.

Do not depend on `SubscribeRoutingStats` in V1. Routing statistics can be disabled in a particular Xray build/configuration, and per-request routing is outside the initial scope.

Reflection is optional and must not be required for normal operation. Direct RPC calls and clear handling of `UNIMPLEMENTED` are authoritative.

## Home Assistant data model

Create one device per Config Entry, for example `Xray API (host:port)`.

### Core entities

- `binary_sensor`: API available;
- `sensor`: derived status with states `online`, `degraded`, `offline`, `unknown`;
- `sensor`: Xray uptime, duration in seconds or another HA-supported duration unit;
- `sensor`: last successful update timestamp.

Status semantics:

- `offline`: transport/API connection failed;
- `degraded`: API is reachable but an observed outbound is dead or one data group failed;
- `online`: API is reachable and all currently observed outbounds are alive;
- `unknown`: no successful snapshot yet.

### Per-outbound entities

Discover outbound tags dynamically from Observatory. Use stable unique IDs based on Config Entry ID plus the original tag; preserve the original tag in attributes.

For each outbound create:

- `binary_sensor`: alive/dead;
- `sensor`: delay;
- `sensor`: cumulative uplink bytes;
- `sensor`: cumulative downlink bytes.

Status attributes must include `outbound_tag`, `last_error_reason`, `last_seen_time`, `last_try_time`, health-ping data, and optionally the raw protobuf mapping.

If an outbound disappears from a later response, keep the entity registered but mark it unavailable rather than rapidly creating/removing entities. Persist the known outbound-tag set in Config Entry data so the same stable entities can be recreated after an HA restart before Observatory returns a fresh snapshot.

### Per-balancer entities

For every balancer tag configured by the user create:

- `sensor`: principle target;
- attributes for override target, query time, and raw response.

An empty target means “no target selected” and is not itself a network error. Use a documented neutral state such as `none` while keeping the entity available.

## Config Flow

Fields:

- `host`, default `192.168.1.1`;
- `port`, default `10085`;
- optional balancer tags entered comma-separated or one per line.

After validating Observatory and Stats, the flow displays the outbounds returned
by Observatory and stores the user's explicit multi-selection; an empty
selection means that no outbound entities are monitored. The same endpoint,
discovery, and selection steps are used by Reconfigure, which updates the
existing Config Entry in place. The native Options Flow edits only balancer
tags and the outbound selection.

Requirements:

- validate connectivity with a 3–5 second gRPC deadline;
- require Observatory and Stats support;
- require Routing only when balancer tags are configured;
- prevent duplicate `host:port` entries;
- provide actionable errors for timeout, refused connection, and `UNIMPLEMENTED`;
- add an Options Flow so balancer tags and outbound selection can be edited later; when tags are added, recheck Routing support, but do not discard edits on timeout or endpoint connection failure.

Do not hardcode the user's outbound or balancer names as integration defaults. They may be used in tests and README examples only.

## Coordinator and failure semantics

Reuse one gRPC channel and run independent RPCs concurrently. A transport failure makes all entities unavailable. A failure in one service group must not hide successful data from other groups:

- Observatory failure: outbound entities unavailable;
- Stats failure: uptime and traffic unavailable;
- Routing failure: balancer entities unavailable.

Retain and expose `last_successful_update`. Throttle repeated connection-error logs. Never replace missing traffic with zero or interpret an empty balancer target as an API outage.

The API is currently plaintext and has no authentication in the supplied configuration. README must require LAN/VPN/firewall restriction and explicitly warn against Internet exposure of port `10085`.

## Repository layout

```text
custom_components/<domain>/
  __init__.py
  api.py
  coordinator.py
  config_flow.py
  entity.py
  sensor.py
  binary_sensor.py
  const.py
  manifest.json
  strings.json
  translations/en.json
  translations/ru.json

tests/
README.md
hacs.json
pyproject.toml
.github/workflows/
LICENSE
```

Use generated protobuf/gRPC stubs from a pinned Xray-core commit. Avoid runtime code generation. The first-release supported HA/Python/architecture scope and the compatible `grpcio` strategy are documented in [`docs/support-matrix.md`](support-matrix.md); custom integration requirements should contain only packages not already guaranteed by HA.

The manifest needs a unique domain, a release version, `config_flow: true`, and an appropriate `integration_type` such as `hub`. See the [Home Assistant integration manifest](https://developers.home-assistant.io/docs/creating_integration_manifest/) and [integration file structure](https://developers.home-assistant.io/docs/creating_integration_file_structure/) documentation.

## Tests and acceptance criteria

Add unit/integration tests with a fake gRPC server for:

- successful coordinator refresh;
- timeout and endpoint down;
- `UNIMPLEMENTED` service/method;
- partial Observatory/Stats/Routing failures;
- alive/dead outbound mapping;
- raw `last_error_reason` and empty target;
- delay/timestamp conversion;
- health-ping mapping and units;
- traffic counters with `reset=false`;
- Xray restart/counter reset;
- dynamic outbound discovery and disappearance;
- Config Flow validation and duplicate endpoint;
- stable unique IDs and device/entity metadata.

CI should run, where supported:

```text
pytest
ruff
mypy (if enabled)
hassfest
HACS validation
```

Acceptance requires that the integration can be installed through HACS, added through the HA UI, and exposes uptime, overall status, five sample outbound statuses, four sample balancer entities, raw Observatory error information, and uplink/downlink counters. No mutating RPC may be called.

## Reference fixture for tests only

The user's current Xray instance exposes `192.168.1.1:10085` and has these Observatory subjects:

```text
vm9-vless
vm11-wg
blanc-vless
vas3k-trojan-fn
vas3k-trojan-us
```

Configured balancers for an integration test fixture:

```text
fallback-to-direct
fallback-to-blanc
fallback-to-vas3k
priority
```

The fixture also uses a 30-second Observatory probe interval and `https://www.google.com/generate_204` as the probe URL. These values must not become public integration defaults.

## Documentation and release

README must explain:

- installation through HACS;
- the required Xray API services;
- example API configuration with placeholders;
- firewall/LAN security requirements;
- every entity and attribute;
- the distinction between Xray uptime and Xkeen process status;
- the distinction between balancer candidate selection and actual per-request routing;
- limitations: no logs, route tracing, balancer enumeration, or control actions;
- that users should inspect Xray/Xkeen logs manually for deeper diagnosis.

Publish as a separate GitHub repository with `hacs.json`, CI, tagged releases, and a clear support matrix. See [HACS publishing guidance](https://www.hacs.xyz/docs/publish/include/).

## Decisions

1. Build a native HA custom integration rather than a REST/MQTT sidecar or YAML command sensors.
2. Position it as `Xray API`; mention Xkeen as a supported deployment scenario.
3. Keep the public package UI-agnostic and free of Bubble Card/popup assumptions.
4. Discover outbound tags from Observatory; accept balancer tags because Xray API does not enumerate them.
5. Use read-only Observatory, Stats, and Routing RPCs only.
6. Poll every approximately 15 seconds; do not implement notifications in V1.
7. Let Home Assistant Recorder provide multi-day history.
8. Leave per-request routing streams, TLS/authentication, logs, and Xkeen-specific management APIs for later versions.

## Open implementation checks

Before each release, the implementing agent must verify:

- final unique HA domain name;
- exact protobuf revision and generated-stub compatibility;
- delay and health-ping units;
- HA-supported units/device classes for duration and data size;
- `total_increasing` behavior after Xray restart;
- dynamic entity lifecycle across HA restarts;
- the supported HA/Python/CPU scope in [`docs/support-matrix.md`](support-matrix.md), including a smoke installation on each claimed architecture;
- whether a lightweight optional text sensor for the raw last-error message is useful in addition to the required diagnostic attribute.
