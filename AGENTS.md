# Repository instructions

## Scope

This repository contains the source of a HACS-compatible Home Assistant custom
integration that monitors a remote Xray core through its gRPC API. Xkeen on a
Keenetic is a supported deployment scenario, not a runtime dependency or a
separate API contract. The detailed design, API references, acceptance
criteria, and open checks are maintained in
[`docs/xray-api-ha-integration-handoff.md`](docs/xray-api-ha-integration-handoff.md).

The final Home Assistant domain must be selected and documented before the
first release. Once selected, preserve the domain, config-entry identifiers,
and public entity unique-ID scheme; do not introduce a second domain as a
shortcut for migrations.

The separate Home Assistant configuration repository and a live HA instance
are outside this repository's implementation scope. Do not edit external
`custom_components` deployment directories, packages, dashboards, prompts,
Xray configuration files, or Xkeen files from this repository. Record any
target-instance configuration or acceptance work in a repository handoff
document instead. The neighboring
`C:\Users\klonw\WebstormProjects\home-assistant-llm-local-intents` repository is
a complete Home Assistant integration and may be used as a structural example
for layout, config-flow patterns, translations, tests, HACS metadata, and CI;
do not copy its LLM-specific behavior or contracts.

## Integration contract

- Provide one Config Entry per Xray endpoint (`host` and `port`); use the
  default port `10085` unless the user changes it.
- Use one shared gRPC channel and read-only RPCs. Observatory and Stats are
  required; Routing is required only when the user configures balancer tags.
- The supported V1 transport is insecure gRPC on a trusted LAN or VPN. Do not
  advertise TLS or authentication until it is implemented as a complete,
  tested feature.
- Call only `ObservatoryService.GetOutboundStatus`,
  `StatsService.GetSysStats`, `StatsService.QueryStats`, and, when configured,
  `RoutingService.GetBalancerInfo`. Never call mutating Xray RPCs.
- Keep the public package UI-agnostic. Do not add Bubble Card, popup,
  dashboard YAML, notifications, automations, shell commands, SSH/SFTP, log
  ingestion, config-file reads, per-request route tracing, or automatic
  balancer enumeration in V1.
- Do not hardcode the user's outbound tags, balancer tags, probe URL, or
  Observatory interval as public defaults. Installation-specific names may
  appear only in test fixtures or clearly marked README examples.
- Do not expose secrets, tokens, complete slot-like values, or noisy repeated
  connection details in ordinary logs. Throttle repeated connection-error
  logging.

## Xray API semantics

### Observatory

Use the pinned official Observatory command/configuration protobuf sources. An
outbound is discoverable only when Observatory returns it; do not infer health
from arbitrary Xray configuration. Preserve the original `outbound_tag`,
`alive`, `delay`, `last_error_reason`, `last_seen_time`, `last_try_time`, and
available health-ping measurements (`all`, `fail`, `deviation`, `average`,
`max`, `min`). Keep `last_error_reason` raw: Xray documents it as not
machine-readable, so do not classify or rewrite it. Validate delay and
health-ping units against the pinned proto and fixtures before release.

### Stats

Use `GetSysStats` for Xray uptime in seconds. Use `QueryStats` with
`reset=false`; query outbound counters under the `outbound>>>` prefix and map
`outbound>>>[tag]>>>traffic>>>uplink` and
`outbound>>>[tag]>>>traffic>>>downlink`. Counters are bytes and must use
Home Assistant's `total_increasing` state class. A missing counter is
`unavailable`, never zero. Document and test behavior after an Xray restart or
counter reset.

### Routing

Accept balancer tags from the Config Flow because Xray does not provide a
reliable balancer-definition list. Do not duplicate selectors, strategies,
costs, or fallback configuration in the integration. Expose
`principle_target` as the strategy-selected candidate (not a guarantee for
every connection) and retain the `override` target, query time, and raw
response as attributes. An empty target means that no target was selected; it
is not an API outage and should use a documented neutral state such as `none`.
`SubscribeRoutingStats` and reflection are not required for normal V1
operation; direct RPCs and explicit `UNIMPLEMENTED` handling are authoritative.

## Home Assistant data model

Create one device per Config Entry, with a name such as `Xray API (host:port)`.
The core entities are:

- `binary_sensor`: API available;
- `sensor`: derived overall status with `online`, `degraded`, `offline`, and
  `unknown` states;
- `sensor`: Xray uptime using an HA-supported duration representation;
- `sensor`: timestamp of the last successful update.

Discover outbound tags from Observatory and create stable unique IDs from the
Config Entry ID plus the original tag. For every observed outbound expose an
alive/dead binary sensor, delay sensor, cumulative uplink sensor, and
cumulative downlink sensor. Preserve the original tag and raw diagnostic
fields as attributes. If an outbound disappears from a later response, retain
its registered entity and mark it unavailable instead of rapidly creating and
removing entities.

For every configured balancer tag expose a principle-target sensor with
override target, query time, and raw-response attributes. The integration must
not claim that a balancer candidate is the route of every individual request.

Overall status semantics are strict: `offline` means transport/API connection
failure; `degraded` means the API is reachable but an observed outbound is dead
or one service group failed; `online` means the API is reachable and all
currently observed outbounds are alive; `unknown` means that no successful
snapshot exists yet.

## Coordinator and failure semantics

Poll approximately every 15 seconds with a `DataUpdateCoordinator` and reuse
one gRPC channel. Run independent Observatory, Stats, and Routing calls
concurrently where practical. A transport failure makes all entities
unavailable. A service-group failure is isolated: Observatory failure affects
outbound entities, Stats failure affects uptime and traffic, and Routing
failure affects balancer entities only. Retain and expose
`last_successful_update`; never replace missing traffic with zero or treat an
empty balancer target as an outage. A successful reload or refresh replaces the
immutable snapshot atomically; a failed refresh preserves the last usable
snapshot while exposing the appropriate unavailable state.

## Config Flow

The user flow must accept `host`, `port` (default `10085`), and optional
balancer tags entered comma-separated or one per line. Validate connectivity
with a 3–5 second gRPC deadline and require Observatory and Stats support;
require Routing only when balancer tags are configured. Reject duplicate
`host:port` entries and provide actionable errors for timeout, refused
connection, and `UNIMPLEMENTED`. Add an Options Flow so balancer tags can be
edited later. Do not make any installation-specific outbound or balancer name
an integration default.

## Source layout and boundaries

Keep Home Assistant imports and version-specific behavior at the integration
boundary; keep pure RPC mapping, validation, and normalization independently
testable where practical. The expected layout is:

- `custom_components/<domain>/api.py` — gRPC channel, deadlines, RPC calls,
  protobuf-to-domain mapping, and transport error normalization;
- `custom_components/<domain>/__init__.py` — Config Entry setup/unload,
  coordinator lifecycle, and device/entity registration;
- `coordinator.py` — polling, concurrent service groups, immutable snapshots,
  partial failures, and last-successful-update state;
- `config_flow.py` — endpoint validation, duplicate detection, and Options Flow;
- `entity.py`, `sensor.py`, and `binary_sensor.py` — device/entity metadata,
  dynamic outbound lifecycle, state classes, and diagnostic attributes;
- `const.py` — domain, defaults, names, and supported service constants;
- `manifest.json`, `strings.json`, `translations/en.json`,
  `translations/ru.json`, `hacs.json` — package metadata and UI text;
- generated protobuf/gRPC stubs — checked in from a pinned Xray-core commit;
  never generate code at runtime;
- `tests/` — unit and fake-gRPC integration tests, including HA API stubs when
  a full HA runtime is not available.
- root `README.md`, `pyproject.toml`, `.github/workflows/`, and `LICENSE` —
  installation, tooling, CI, and distribution metadata.

Keep the package dependency list limited to libraries not already guaranteed
by the supported Home Assistant versions. Verify supported HA versions and CPU
architectures before choosing a gRPC dependency strategy.
The manifest must expose a unique domain, a release version, `config_flow: true`,
and an appropriate `integration_type` such as `hub`.

## Documentation and security

README must explain HACS and manual installation, required Xray services,
placeholder-based API configuration, LAN/VPN/firewall restrictions, and the
warning never to expose plaintext port `10085` to the Internet. Document every
entity and attribute, Xray uptime versus Xkeen process status, balancer
candidate selection versus actual per-request routing, and the V1 limitations
(no logs, route tracing, enumeration, or control actions). Tell users to
inspect Xray/Xkeen logs manually for deeper diagnosis.

## Verification

Run these checks from the repository root before reporting implementation
completion (adjust `<domain>` only after the final domain is documented):

```bash
python -m compileall -q custom_components/<domain>
python -m pytest tests
git diff --check
```

Also run repository-configured `ruff`, `mypy` (if enabled), Hassfest, and HACS
validation. CI may provide the latter checks, but local failures must be
reported rather than silently ignored. Tests must cover successful refresh,
timeouts/endpoint-down, `UNIMPLEMENTED`, partial service failures, outbound
mapping and disappearance, raw Observatory errors, delay/timestamp and
health-ping units, `reset=false` traffic counters, restart/counter reset,
Config Flow validation and duplicate endpoints, stable IDs, and device/entity
metadata. Before the first release additionally verify the exact protobuf
revision, HA-supported duration/data-size units, dynamic entity lifecycle
across restarts, and whether an optional raw-error text sensor is useful.
Acceptance also requires installation through HACS, adding the integration
through the HA UI, exposure of uptime/overall status/outbound and balancer
entities, and proof that no mutating RPC is called.

Inspect the complete diff and preserve unrelated user changes. For executable
source, test, dependency, runtime configuration, API, or infrastructure
changes, complete the repository's required independent review workflow before
claiming completion. Documentation-only edits do not require that reviewer
unless the user explicitly asks for one.
