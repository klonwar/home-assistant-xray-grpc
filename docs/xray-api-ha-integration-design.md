# Xray API Home Assistant Integration Design

## Status

Validated with the user during the brainstorming phase. This document is the implementation design for the multi-step configuration and monitoring-selection flow and must be kept aligned with the repository handoff.

## Understanding summary

- Build a HACS-compatible Home Assistant custom integration for a remote Xray core reachable through insecure gRPC; Xkeen is a supported deployment scenario, not a runtime dependency.
- Create one Config Entry per `host:port`, with host `192.168.1.1` and port `10085` as initial form defaults; balancer tags are entered manually and monitoring outbounds are explicitly selected.
- Use only read-only Observatory, Stats, and conditional Routing RPCs over one shared channel, polling approximately every 15 seconds.
- Expose core availability/status/uptime/update-time entities, dynamically discovered outbound entities, and principle-target sensors for configured balancers.
- Isolate service-group failures, preserve the last usable immutable snapshot, retain disappeared outbound entities as unavailable, and never replace missing counters with zero.
- Use one shared multi-step wizard for new entries and Reconfigure, with a native Options Flow for editing monitoring settings; include pure mapping tests, fake-gRPC coordinator tests, HA-boundary tests, translations, metadata, README, and CI/HACS/Hassfest hooks where supported.
- Keep V1 UI-agnostic: no TLS/authentication claims, logs, SSH/SFTP, config reads, notifications, mutating RPCs, route tracing, or balancer enumeration.

## Assumptions

- The final Home Assistant domain is `xray_api`; once released, the domain, config-entry identifiers, and public unique-ID scheme are immutable.
- V1 uses plaintext gRPC only on a trusted LAN or VPN. TLS and authentication are future features only when implemented and tested completely.
- A complete Home Assistant runtime may not be available locally; pure logic and fake-gRPC tests are therefore first-class verification, while live HA acceptance remains an external handoff task.
- The pinned Xray protobuf revision is `v26.7.28` at commit `5ca6f4b7d4dc20a881d4330e498892697627ec0c`; the first-release HA/Python/architecture scope is documented in [`docs/support-matrix.md`](support-matrix.md).
- External Home Assistant configuration repositories and live instances are outside this repository and will not be edited.
- Xray does not provide a reliable balancer-definition list for this V1 contract, so balancer tags remain manual input and `RoutingService.ListRule` is not added for discovery.
- Endpoint identity (`host` and `port`) remains in `Config Entry.data`; monitoring preferences (`balancer_tags` and `monitored_outbound_tags`) live in `Config Entry.options`.
- A missing `monitored_outbound_tags` option means legacy "monitor all" behavior; a present empty tuple/list means explicitly monitor no outbounds.
- The same wizard steps are shared by Config Flow user setup and Reconfigure, while Options Flow follows Home Assistant's native options persistence model.

## Architecture

The `xray_api` integration creates one runtime context per Config Entry: one `grpc.aio.Channel`, API client, `XrayCoordinator`, and platform entities. Unload stops the coordinator, removes entities, and closes the channel.

`api.py` is HA-agnostic where practical. It contains checked-in generated stubs, deadlines, the four permitted RPC groups, gRPC error normalization, and typed dataclasses for outbound statuses, health-ping measurements, traffic counters, balancer results, and service errors. It does not call mutating Xray methods or expose secrets in ordinary logs.

`coordinator.py` runs Observatory, Stats, and conditional Routing concurrently on one channel. Each refresh produces an immutable snapshot containing group status, service data, the previous known outbound-tag set, and `last_successful_update`. A failed refresh does not erase the last usable snapshot; only the affected group becomes unavailable.

The HA boundary consists of `__init__.py`, `entity.py`, `sensor.py`, `binary_sensor.py`, and the flow handlers. It owns Config Entry lifecycle, device/entity metadata, dynamic registration, state classes, diagnostics attributes, and flow persistence. A shared flow mixin implements endpoint input, connectivity/discovery, and outbound selection; `async_step_user` and `async_step_reconfigure` use the same steps, while `async_step_init` in Options Flow edits only monitoring preferences. Dynamic outbound entities are created only for explicitly monitored tags (or all tags in legacy mode), their known tags are persisted in Config Entry data, and they are recreated as unavailable after an HA restart or later Observatory omission.

## Data flow and failure semantics

The multi-step Config/Reconfigure Flow normalizes `host`, `port`, and manual balancer tags (comma-separated or one per line), rejects duplicate normalized endpoints except for the entry being reconfigured, and validates connectivity with a 3–5 second deadline. Observatory and Stats are required; Routing is tested for every configured balancer tag. A successful validation returns the current Observatory outbound tags for a multiple-choice selector. A failed validation leaves a new entry uncreated or an existing entry unchanged. Options Flow uses the same normalization and Routing checks for monitoring edits, but stores only `balancer_tags` and `monitored_outbound_tags` in options; offline edits remain saveable while a later coordinator refresh exposes unavailable/degraded state.

New entries persist `host` and `port` in data and persist the explicit outbound selection in options, including an empty selection. Reconfigure first validates the proposed endpoint and obtains its outbound list, then updates the existing Config Entry atomically and reloads it so the `entry_id` and stable entity IDs remain unchanged. For a legacy entry, the selector is initially populated with all available outbounds; for an endpoint change, only old selected tags present in the new response are preselected.

Observatory preserves each original tag, `alive`, `delay`, raw `last_error_reason`, `last_seen_time`, `last_try_time`, and health-ping values. Stats calls `GetSysStats` and `QueryStats(reset=false)` and maps exact `outbound>>>[tag]>>>traffic>>>uplink/downlink` keys. Missing counters are unavailable; a real zero remains zero. Routing calls `GetBalancerInfo` per configured tag; an empty `principle_target` becomes neutral state `none`, not an outage, while `override`, query time, and raw response remain attributes.

Overall status is strict: `unknown` before any successful snapshot; `offline` for transport/API failure; `degraded` when the API is reachable but a monitored outbound is dead or any service group failed; `online` when the API is reachable and all monitored outbounds are alive. An explicit empty monitored-outbound selection does not itself degrade an otherwise reachable API. Unselected outbounds do not affect status, entities, or filtered traffic snapshots. Group availability controls only that group’s entities.

## Entity model

Each Config Entry creates one device named `Xray API (host:port)`. Unique IDs are entry-scoped and include a collision-safe encoding of the original outbound or balancer tag. Original tags remain diagnostic attributes.

Core entities are API availability (`binary_sensor`), overall status (`sensor`), Xray uptime from `GetSysStats` (`sensor` with a verified HA duration representation), and last successful update (`sensor` with timestamp device class).

Every monitored outbound receives an alive/dead binary sensor, delay sensor, cumulative uplink sensor, and cumulative downlink sensor. Traffic sensors use bytes and `total_increasing`. Missing counters and failed Observatory/Stats groups are unavailable rather than zero or stale-success claims. A monitored tag absent from the latest Observatory response retains its registered entities but becomes unavailable; an unselected tag never creates entities. Outbound attributes include raw Observatory diagnostics and health-ping fields.

Every configured balancer receives a principle-target sensor. Empty targets are `none`; attributes include override target, query time, and raw response. The integration never claims that a candidate is the route of every request.

## Testing and verification

Pure mapping tests cover delay and health-ping units, raw errors, timestamps, unknown fields, empty routing targets, exact traffic keys, `reset=false`, missing counters, zero values, and restart/counter-reset sequences. Fake-gRPC flow tests cover the two-step add/reconfigure wizard, default endpoint values, manual balancer validation, selector options, empty selection, timeout/connection/`UNIMPLEMENTED` errors, duplicate reconfigure endpoints, legacy monitor-all migration, and stable entry updates. Coordinator/entity tests cover filtering, disappearance, selected-tag additions/removals, empty-selection status, and stable IDs. Full deadline/status-code matrix coverage, HA platform setup/unload, and live entity registration remain explicit release-acceptance checks because the full HA runtime is not a repository dependency.

HA-boundary stubs cover normalization, duplicate endpoints (including equivalent IPv6 literals), Options Flow behavior, device metadata, entity classes and state classes, stable unique IDs, availability, and status transitions. Before completion run `python -m compileall -q custom_components/xray_api`, `python -m pytest tests`, `git diff --check`, and any configured `ruff`, `mypy`, Hassfest, and HACS checks. Live HACS installation and HA/Xray acceptance are external handoff work.

## Decision log

1. **Native HA integration with checked-in generated stubs.** A sidecar or manual wire-message implementation was rejected because it adds deployment/API boundaries or risks protobuf drift.
2. **One shared channel and concurrent service groups.** This satisfies the read-only gRPC contract while keeping polling latency bounded and failures isolatable.
3. **Immutable last-known snapshot with partial group status.** A failed refresh must not erase usable data; missing data must remain unavailable rather than becoming a fabricated zero.
4. **Stable entry-scoped IDs and retained dynamic entities.** Registry continuity is more important than deleting entities when Observatory temporarily omits an outbound; persisted tags also allow the entity objects to return after HA restarts.
5. **Neutral `none` balancer state.** An empty candidate is a valid routing result, not an API outage; raw response data remains available for diagnosis.
6. **Pure, fake-gRPC, and HA-boundary test layers.** Each layer can run without requiring a live HA instance, while external runtime acceptance remains explicit and separate.
7. **UI-agnostic V1.** Dashboards, Bubble Card, notifications, route tracing, logs, control actions, TLS/authentication, and automatic balancer enumeration remain outside the initial contract.
8. **Manual balancer tags.** Because V1 has no reliable read-only balancer-definition enumeration contract, users enter balancer tags on the first screen and the integration validates them with `GetBalancerInfo`; no `ListRule` discovery is introduced.
9. **Explicit outbound monitoring selection.** New and edited entries monitor only the selected Observatory tags; an explicit empty selection monitors none, while a missing option preserves legacy monitor-all behavior for existing entries.
10. **Native HA flow split.** Config Flow and Reconfigure share the complete endpoint/discovery/selection wizard; Options Flow edits monitoring preferences using `options`, and endpoint identity remains in `data`.
11. **In-place endpoint reconfigure.** A validated host/port change updates the existing entry and reloads the integration instead of deleting/recreating it, preserving entry-scoped IDs and user automations.
