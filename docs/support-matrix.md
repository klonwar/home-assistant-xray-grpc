# V1 support matrix

This is the support scope for the first `xray_api` release, reviewed on 2026-09-03.

| Area | Supported V1 scope | Verification / limitation |
| --- | --- | --- |
| Home Assistant | 2026.9.x and later | Home Assistant 2026.9.0 requires Python 3.14.2 or newer; the integration follows the runtime bundled by HA. |
| Installation | Home Assistant OS and Home Assistant Container | Core and Supervised installation methods are no longer the supported HA deployment targets. |
| CPU architecture | 64-bit `amd64` and `aarch64` | `grpcio==1.83.1` publishes CPython 3.14 wheels for both Linux architectures. |
| Python | CPython 3.14.2+ as supplied by HA | The repository CI runtime job uses Python 3.14.2 and the declared dependency floor. |
| gRPC dependency | `grpcio>=1.83.1` | The generated Xray bindings require the 1.83.1 gRPC runtime API. |
| Protobuf dependency | `protobuf>=4.25.0` | Generated bindings are checked in; code generation never runs on the HA host. |
| Transport | Insecure gRPC on a trusted LAN or VPN | TLS and authentication are intentionally outside V1. |

The supported architecture decision follows Home Assistant's current
deployment guidance: only 64-bit `amd64` and `aarch64` targets are claimed;
`i386`, `armhf`, and `armv7` are not supported. See the [Home Assistant
system-architecture guidance](https://www.home-assistant.io/more-info/unsupported/system_architecture/)
and the [installation-method and architecture deprecation notice](https://www.home-assistant.io/blog/2025/05/22/deprecating-core-and-supervised-installation-methods-and-32-bit-systems/).

The dependency floor is based on the checked-in generated stubs and the
published [grpcio 1.83.1 wheels](https://pypi.org/project/grpcio/1.83.1/),
including CPython 3.14 manylinux wheels for `x86_64` and `aarch64`.

## Release verification

The repository checks the Python/dependency floor on Linux `amd64` in CI.
Hassfest and HACS validation run in the separate validation workflow. Before
publishing a release, perform a smoke installation on one HA OS or Container
instance for each claimed architecture and record the result in the release
notes. A platform without a successful installation must be removed from the
claimed matrix rather than silently treated as supported.

