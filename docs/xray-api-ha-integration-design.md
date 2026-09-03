# Xray API Home Assistant Integration Design

## Status

Validated with the user during the brainstorming phase. This document is the implementation design for V1 and must be kept aligned with the repository handoff.

## Understanding summary

- Build a HACS-compatible Home Assistant custom integration for a remote Xray core reachable through insecure gRPC; Xkeen is a supported deployment scenario, not a runtime dependency.
- Create one Config Entry per `host:port`, with port `10085` by default and optional user-supplied balancer tags editable through an Options Flow.
- Use only read-only Observatory, Stats, and conditional Routing RPCs over one shared channel, polling approximately every 15 seconds.
- Expose core availability/status/uptime/update-time entities, dynamically discovered outbound entities, and principle-target sensors for configured balancers.
- Isolate service-group failures, preserve the last usable immutable snapshot, retain disappeared outbound entities as unavailable, and never replace missing counters with zero.
- Include pure mapping tests, fake-gRPC coordinator tests, HA-boundary tests, translations, metadata, README, and CI/HACS/Hassfest hooks where supported.
- Keep V1 UI-agnostic: no TLS/authentication claims, logs, SSH/SFTP, config reads, notifications, mutating RPCs, route tracing, or balancer enumeration.

## Assumptions

- The final Home Assistant domain is `xray_api`; once released, the domain, config-entry identifiers, and public unique-ID scheme are immutable.
- V1 uses plaintext gRPC only on a trusted LAN or VPN. TLS and authentication are future features only when implemented and tested completely.
- A complete Home Assistant runtime may not be available locally; pure logic and fake-gRPC tests are therefore first-class verification, while live HA acceptance remains an external handoff task.
- The pinned Xray protobuf revision is `v26.7.28` at commit `5ca6f4b7d4dc20a881d4330e498892697627ec0c`; the first-release HA/Python/architecture scope is documented in [`docs/support-matrix.md`](support-matrix.md).
- External Home Assistant configuration repositories and live instances are outside this repository and will not be edited.

## Architecture

The `xray_api` integration creates one runtime context per Config Entry: one `grpc.aio.Channel`, API client, `XrayCoordinator`, and platform entities. Unload stops the coordinator, removes entities, and closes the channel.

`api.py` is HA-agnostic where practical. It contains checked-in generated stubs, deadlines, the four permitted RPC groups, gRPC error normalization, and typed dataclasses for outbound statuses, health-ping measurements, traffic counters, balancer results, and service errors. It does not call mutating Xray methods or expose secrets in ordinary logs.

`coordinator.py` runs Observatory, Stats, and conditional Routing concurrently on one channel. Each refresh produces an immutable snapshot containing group status, service data, the previous known outbound-tag set, and `last_successful_update`. A failed refresh does not erase the last usable snapshot; only the affected group becomes unavailable.

The HA boundary consists of `__init__.py`, `entity.py`, `sensor.py`, and `binary_sensor.py`. It owns Config Entry lifecycle, device/entity metadata, dynamic registration, state classes, and diagnostics attributes. Dynamic outbound entities are created when first observed, their tags are persisted in Config Entry data, and they are recreated as unavailable after an HA restart or later Observatory omission.

## Data flow and failure semantics

Config Flow normalizes `host`, `port`, and tags (comma-separated or one per line), rejects duplicate normalized endpoints, and validates connectivity with a 3–5 second deadline. Observatory and Stats are required; Routing is tested only when tags are configured. Timeout, refused/unavailable, and `UNIMPLEMENTED` failures become actionable form errors. Options Flow rechecks Routing when tags are added, rejects a definitive `UNIMPLEMENTED` response, and still persists offline edits so the coordinator can expose unavailable/degraded state until recovery.

Observatory preserves each original tag, `alive`, `delay`, raw `last_error_reason`, `last_seen_time`, `last_try_time`, and health-ping values. Stats calls `GetSysStats` and `QueryStats(reset=false)` and maps exact `outbound>>>[tag]>>>traffic>>>uplink/downlink` keys. Missing counters are unavailable; a real zero remains zero. Routing calls `GetBalancerInfo` per configured tag; an empty `principle_target` becomes neutral state `none`, not an outage, while `override`, query time, and raw response remain attributes.

Overall status is strict: `unknown` before any successful snapshot; `offline` for transport/API failure; `degraded` when the API is reachable but an outbound is dead or any service group failed; `online` when the API is reachable and all currently observed outbounds are alive. Group availability controls only that group’s entities.

## Entity model

Each Config Entry creates one device named `Xray API (host:port)`. Unique IDs are entry-scoped and include a collision-safe encoding of the original outbound or balancer tag. Original tags remain diagnostic attributes.

Core entities are API availability (`binary_sensor`), overall status (`sensor`), Xray uptime from `GetSysStats` (`sensor` with a verified HA duration representation), and last successful update (`sensor` with timestamp device class).

Every previously observed outbound receives an alive/dead binary sensor, delay sensor, cumulative uplink sensor, and cumulative downlink sensor. Traffic sensors use bytes and `total_increasing`. Missing counters and failed Observatory/Stats groups are unavailable rather than zero or stale-success claims. Outbound attributes include raw Observatory diagnostics and health-ping fields.

Every configured balancer receives a principle-target sensor. Empty targets are `none`; attributes include override target, query time, and raw response. The integration never claims that a candidate is the route of every request.

## Testing and verification

Pure mapping tests cover delay and health-ping units, raw errors, timestamps, unknown fields, empty routing targets, exact traffic keys, `reset=false`, missing counters, zero values, and restart/counter-reset sequences. Fake-gRPC tests cover success, endpoint-down normalization, one shared channel, dynamic discovery/disappearance, and absence of mutating RPCs. Full deadline/status-code matrix coverage, HA platform setup/unload, and live entity registration remain explicit release-acceptance checks because the full HA runtime is not a repository dependency.

HA-boundary stubs cover normalization, duplicate endpoints (including equivalent IPv6 literals), Options Flow behavior, device metadata, entity classes and state classes, stable unique IDs, availability, and status transitions. Before completion run `python -m compileall -q custom_components/xray_api`, `python -m pytest tests`, `git diff --check`, and any configured `ruff`, `mypy`, Hassfest, and HACS checks. Live HACS installation and HA/Xray acceptance are external handoff work.

## Decision log

1. **Native HA integration with checked-in generated stubs.** A sidecar or manual wire-message implementation was rejected because it adds deployment/API boundaries or risks protobuf drift.
2. **One shared channel and concurrent service groups.** This satisfies the read-only gRPC contract while keeping polling latency bounded and failures isolatable.
3. **Immutable last-known snapshot with partial group status.** A failed refresh must not erase usable data; missing data must remain unavailable rather than becoming a fabricated zero.
4. **Stable entry-scoped IDs and retained dynamic entities.** Registry continuity is more important than deleting entities when Observatory temporarily omits an outbound; persisted tags also allow the entity objects to return after HA restarts.
5. **Neutral `none` balancer state.** An empty candidate is a valid routing result, not an API outage; raw response data remains available for diagnosis.
6. **Pure, fake-gRPC, and HA-boundary test layers.** Each layer can run without requiring a live HA instance, while external runtime acceptance remains explicit and separate.
7. **UI-agnostic V1.** Dashboards, Bubble Card, notifications, route tracing, logs, control actions, TLS/authentication, and automatic balancer enumeration remain outside the initial contract.
