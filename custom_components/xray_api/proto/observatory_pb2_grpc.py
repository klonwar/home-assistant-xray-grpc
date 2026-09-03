"""Compatibility exports for canonical generated Observatory grpc bindings."""

from .app.observatory.command.command_pb2_grpc import (  # noqa: F401
    ObservatoryServiceServicer,
    ObservatoryServiceStub,
    add_ObservatoryServiceServicer_to_server,
)

