# Pinned Xray command sources

These command stubs mirror Xray-core `v26.7.28` at commit `5ca6f4b7d4dc20a881d4330e498892697627ec0c` and were generated with `grpcio-tools`. The canonical generated artifacts live under `app/` and `common/`; the top-level modules are compatibility re-exports used by the integration. The generated grpc bindings require `grpcio>=1.83.1`.

The source contracts are the official Xray Observatory, Stats, and Routing command protobufs. Only the request/response messages and RPC methods used by the integration are represented in the checked-in Python modules; no mutating RPC is exposed by the client. Runtime descriptor generation is not used.
