"""Constants for the Xray API integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "xray_api"
NAME = "Xray API"
VERSION = "0.1.0"
DEFAULT_PORT = 10085
DEFAULT_SCAN_INTERVAL = timedelta(seconds=15)
DEFAULT_RPC_TIMEOUT = 4.0
CONFIG_FLOW_TIMEOUT = 5.0

CONF_HOST = "host"
CONF_PORT = "port"
CONF_BALANCER_TAGS = "balancer_tags"
# Internal Config Entry data used to recreate dynamic entities after restart.
CONF_OUTBOUND_TAGS = "outbound_tags"

ATTR_OUTBOUND_TAG = "outbound_tag"
ATTR_BALANCER_TAG = "balancer_tag"
ATTR_LAST_ERROR_REASON = "last_error_reason"
ATTR_LAST_SEEN_TIME = "last_seen_time"
ATTR_LAST_TRY_TIME = "last_try_time"
ATTR_HEALTH_PING = "health_ping"
ATTR_OVERRIDE = "override"
ATTR_QUERY_TIME = "query_time"
ATTR_RAW_RESPONSE = "raw_response"
ATTR_SERVICE_ERRORS = "service_errors"

STATUS_ONLINE = "online"
STATUS_DEGRADED = "degraded"
STATUS_OFFLINE = "offline"
STATUS_UNKNOWN = "unknown"

SERVICE_OBSERVATORY = "observatory"
SERVICE_STATS = "stats"
SERVICE_ROUTING = "routing"
