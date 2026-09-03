# V1 support matrix

This is the support scope for the first `xray_api` release, reviewed on 2026-09-03.

| Area | Supported V1 scope | Verification / limitation |
| --- | --- | --- |
| Home Assistant | 2026.9.x and later | Home Assistant 2026.9.0 requires Python 3.14.2 or newer; the integration follows the runtime bundled by HA. |
| Installation | Home Assistant OS and Home Assistant Container | Core and Supervised installation methods are no longer the supported HA deployment targets. |
| CPU architecture | 64-bit `amd64` and `aarch64` | Uses the gRPC runtime bundled by Home Assistant for the selected architecture. |
| Python | CPython 3.14.2+ as supplied by HA | The repository CI runtime job uses Python 3.14.2 and the HA runtime compatibility floor. |
| gRPC dependency | Home Assistant's bundled `grpcio` (1.78.0 in HA 2026.9) | Generated bindings are compatible with the bundled runtime; the manifest declares no `grpcio` requirement, avoiding a global dependency conflict. |
| Protobuf dependency | Home Assistant's bundled `protobuf` | Generated bindings are checked in; code generation never runs on the HA host and no protobuf package installation is requested. |
| Transport | Insecure gRPC on a trusted LAN or VPN | TLS and authentication are intentionally outside V1. |

The supported architecture decision follows Home Assistant's current
deployment guidance: only 64-bit `amd64` and `aarch64` targets are claimed;
`i386`, `armhf`, and `armv7` are not supported. See the [Home Assistant
system-architecture guidance](https://www.home-assistant.io/more-info/unsupported/system_architecture/)
and the [installation-method and architecture deprecation notice](https://www.home-assistant.io/blog/2025/05/22/deprecating-core-and-supervised-installation-methods-and-32-bit-systems/).

The generated gRPC bindings retain the Xray service contracts while using a
`grpcio` 1.78.0 compatibility floor, matching the runtime bundled by HA
2026.9. Keeping both packages out of the integration manifest is important:
Home Assistant resolves its global dependency set before loading a custom
integration, so a stricter integration requirement can make the Config Flow
fail with HTTP 500 before it is displayed.

## Release verification

The repository checks the Python/runtime compatibility floor on Linux `amd64` in CI.
Hassfest and HACS validation run in the separate validation workflow. Before
publishing a release, perform a smoke installation on one HA OS or Container
instance for each claimed architecture and record the result in the release
notes. A platform without a successful installation must be removed from the
claimed matrix rather than silently treated as supported.
