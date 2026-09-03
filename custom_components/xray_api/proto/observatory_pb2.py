"""Compatibility exports for the canonical generated Observatory modules."""

from .app.observatory.command.command_pb2 import (  # noqa: F401
    Config,
    GetOutboundStatusRequest,
    GetOutboundStatusResponse,
)
from .app.observatory.config_pb2 import (  # noqa: F401
    HealthPingMeasurementResult,
    ObservationResult,
    OutboundStatus,
)

__all__ = [
    "Config",
    "GetOutboundStatusRequest",
    "GetOutboundStatusResponse",
    "HealthPingMeasurementResult",
    "ObservationResult",
    "OutboundStatus",
]

