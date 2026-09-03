from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "xray_api"


def test_package_structure_and_manifest() -> None:
    for filename in (
        "__init__.py", "api.py", "coordinator.py", "config_flow.py", "entity.py",
        "sensor.py", "binary_sensor.py", "const.py", "manifest.json", "strings.json",
    ):
        assert (INTEGRATION / filename).is_file(), filename
    assert (INTEGRATION / "translations" / "en.json").is_file()
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    assert manifest["domain"] == "xray_api"
    assert manifest["config_flow"] is True
    assert manifest["integration_type"] == "hub"
    assert manifest["version"] == "0.1.0"


def test_runtime_dependencies_match_home_assistant() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    requirements = manifest.get("requirements", [])
    assert not any(requirement.lower().startswith("grpcio") for requirement in requirements)

    # HA 2026.9 ships grpcio 1.78.0. The checked-in generated bindings must
    # not reject that runtime before the config flow can be loaded.
    minimum = (1, 78, 0)
    for stub in INTEGRATION.rglob("*_grpc.py"):
        match = re.search(r"GRPC_GENERATED_VERSION = ['\"](\d+)\.(\d+)\.(\d+)", stub.read_text())
        if match:
            generated = tuple(int(part) for part in match.groups())
            assert generated <= minimum, stub
