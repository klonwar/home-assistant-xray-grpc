from __future__ import annotations

import json
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

