"""Compatibility exports for canonical generated Stats grpc bindings."""

from .app.stats.command.command_pb2_grpc import (  # noqa: F401
    StatsServiceServicer,
    StatsServiceStub,
    add_StatsServiceServicer_to_server,
)

