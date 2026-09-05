# Xray API for Home Assistant

This HACS-compatible custom integration monitors a remote Xray core through its read-only gRPC API. Xkeen on Keenetic is a supported deployment scenario, but Xkeen is not a runtime dependency or a separate API contract.

## Installation

In HACS, add this GitHub repository as a custom repository of type **Integration**, install **Xray API**, and restart Home Assistant. For manual installation, copy `custom_components/xray_api` into the Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Configure

Add **Xray API** through the Home Assistant UI. The first step accepts the Xray API host (default `192.168.1.1`), port (default `10085`), and optional balancer tags separated by commas or lines. After Observatory and Stats are validated, the next step lists the outbounds returned by Observatory; select only the outbounds that should get monitoring entities. An empty selection is valid and means that no outbound entities are created. Use placeholders such as `xray.example.lan`; installation-specific outbound and balancer names are not integration defaults.

The same endpoint-and-selection wizard is available from **Reconfigure**. It updates the existing Config Entry in place, preserving its entry ID and stable entity IDs while reloading the integration. The gear **Options Flow** edits balancer tags and the outbound selection without changing the endpoint; opening it refreshes the selectable outbound list directly from Observatory. Existing entries created before outbound selection was introduced keep the legacy monitor-all behavior until the selection is explicitly saved. If Observatory is temporarily unavailable, the last known list is shown so the options can still be edited.

The Xray API must expose these services:

- `ObservatoryService.GetOutboundStatus`
- `StatsService.GetSysStats`
- `StatsService.QueryStats`
- `RoutingService.GetBalancerInfo` (only when balancer tags are configured)

V1 uses insecure gRPC. Restrict port `10085` to a trusted LAN or VPN with firewall rules. **Never expose plaintext Xray API port `10085` directly to the Internet.** TLS and authentication are not advertised until implemented as a complete, tested feature.

## Entities

One device is created per Config Entry, named `Xray API (host:port)`.

- **API available** — connectivity to the Xray API.
- **Status** — `online`, `degraded`, `offline`, or `unknown`. `offline` is transport/API failure; `degraded` means the API is reachable but an outbound is dead or a service group failed; `online` means every currently observed outbound is alive; `unknown` means no successful snapshot exists.
- **Xray uptime** — seconds reported by `GetSysStats`; this is Xray core uptime, not Xkeen process uptime or host uptime.
- **Last successful update** — timestamp of the last refresh that obtained usable service data.
- **Outbound alive** — one binary sensor per tag returned by Observatory.
- **Outbound delay** — Observatory probe delay in milliseconds.
- **Outbound uplink/downlink** — cumulative byte counters from `QueryStats(reset=false)`, with `total_increasing` state class. A missing counter is unavailable, never zero; a counter reset after an Xray restart is represented as unavailable until a valid new counter is observed.
- **Balancer principle target** — the strategy-selected candidate returned by `GetBalancerInfo`, or `none` when no candidate was selected. This candidate is not a guarantee for every individual connection.

Outbound entities are discovered only from Observatory. Their unique IDs include the Config Entry ID and original tag. Known tags are persisted in the Config Entry so entities can be recreated after an HA restart even before Observatory recovers. If an outbound disappears from a later response, its entities remain registered and become unavailable rather than being removed.

If the endpoint is temporarily down during HA startup, the Config Entry still loads: the API-available entity reports unavailable connectivity, the overall status starts as `unknown`, and periodic coordinator refreshes can recover the endpoint without re-adding the integration.

Outbound diagnostic attributes preserve the original tag, raw `last_error_reason`, raw and UTC-converted last-seen/last-try timestamps, delay units, and health-ping measurements (`all`, `fail`, `deviation`, `average`, `max`, `min`, in milliseconds). Balancer attributes preserve the override target, query time, all returned principle targets, and the raw response.

## Failure semantics and limitations

Observatory, Stats, and Routing are polled concurrently every approximately 15 seconds over one shared gRPC channel. A failed service group makes only its entities unavailable while the last usable snapshot is retained. Repeated connection errors are throttled and ordinary logs do not contain complete connection details, secrets, or counter values.

This integration is intentionally UI-agnostic. It does not provide dashboards, Bubble Card popups, notifications, automations, route tracing, balancer enumeration, log ingestion, SSH/SFTP, shell commands, config-file reads, or control/mutating Xray RPCs. Inspect Xray and Xkeen logs manually for deeper diagnosis.

## Release automation

Release Please manages stable versions on `main` only. Use Conventional Commit prefixes such as `fix:` for patch releases, `feat:` for minor releases, and a `!` suffix (or `BREAKING CHANGE:` footer) for breaking changes. Do not edit the integration version manually in ordinary feature or fix PRs.

Beta releases are manual and do not create commits. In the Actions tab, select the `main` branch as the workflow revision, run `[Release] Beta`, and enter the feature branch or commit SHA in `source_ref`. The workflow rejects a workflow revision from any other branch. It uses `ietf-tools/semver-action` to calculate the next SemVer line, appends a UTC timestamp plus the unique GitHub run ID (for example `-beta.20260904123045.21920458098`), and uses `ncipollo/release-action` to create a GitHub prerelease on the exact source SHA. It does not change `manifest.json`, `CHANGELOG.md`, or any branch. Because a beta is intentionally published without a version-changing commit, the checked-out manifest keeps the stable version while HACS displays the prerelease version from the GitHub release tag. HACS users must enable pre-release updates to receive beta versions.

For predictable beta versioning, release-impacting commits must use `feat:`, `fix:`, or a breaking marker (`!` or `BREAKING CHANGE:`). Other commit types do not start a beta release. After this migration is merged, delete the obsolete `beta` branch; keep its historical tags.

After testing, merge the feature branch into `main` (prefer squash merge), and let the stable Release Please workflow open and publish the regular `vX.Y.Z` release. The workflows require the repository Actions secret `RELEASE_PLEASE_TOKEN` for stable releases, a fine-grained token with repository-scoped `contents`, `issues`, and pull-request write permissions. Review CI before merging each stable Release Please PR.

## Development

The checked-in command stubs mirror Xray-core v26.7.28 (commit `5ca6f4b7d4dc20a881d4330e498892697627ec0c`). The integration uses the `grpcio` and `protobuf` runtimes supplied by supported Home Assistant releases; it does not ask Home Assistant to install conflicting global versions. Run from the repository root:

```bash
python -m compileall -q custom_components/xray_api
python -m pytest tests
git diff --check
```

The first-release runtime scope is documented in [docs/support-matrix.md](docs/support-matrix.md): Home Assistant 2026.9.x+, HA OS or Container, and 64-bit `amd64`/`aarch64`. CI runs Ruff, the dependency-floor smoke tests, Hassfest, and HACS validation. Live HACS installation, adding the integration through the HA UI, and acceptance against a real Xray endpoint remain deployment-level checks; the support matrix requires recording those smoke-install results before publishing a release.
