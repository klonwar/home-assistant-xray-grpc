from __future__ import annotations

import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "xray_api"
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


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
    assert isinstance(manifest["version"], str)
    assert SEMVER_PATTERN.fullmatch(manifest["version"])


def test_runtime_dependencies_match_home_assistant() -> None:
    manifest = json.loads((INTEGRATION / "manifest.json").read_text())
    requirements = manifest.get("requirements", [])
    assert not any(requirement.lower().startswith("grpcio") for requirement in requirements)
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "grpcio==1.78.0 protobuf==6.32.0" in ci

    # HA 2026.9 ships grpcio 1.78.0. The checked-in generated bindings must
    # not reject that runtime before the config flow can be loaded.
    minimum = (1, 78, 0)
    for stub in INTEGRATION.rglob("*_grpc.py"):
        match = re.search(r"GRPC_GENERATED_VERSION = ['\"](\d+)\.(\d+)\.(\d+)", stub.read_text())
        if match:
            generated = tuple(int(part) for part in match.groups())
            assert generated <= minimum, stub


def test_local_brand_icon_is_hacs_compatible() -> None:
    icon = INTEGRATION / "brand" / "icon.png"
    data = icon.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (256, 256)


def test_release_please_manifest_tracks_integration_version() -> None:
    """Keep Release Please's stable bootstrap version aligned with the integration."""
    integration_manifest = json.loads(
        (INTEGRATION / "manifest.json").read_text(encoding="utf-8")
    )
    release_manifest = json.loads(
        (ROOT / ".release-please-manifest.json").read_text(encoding="utf-8")
    )
    release_config = json.loads(
        (ROOT / "release-please-config.json").read_text(encoding="utf-8")
    )

    assert release_manifest["."] == integration_manifest["version"]
    assert release_config["versioning"] == "default"
    assert "prerelease-type" not in release_config
    assert "prerelease" not in release_config
    package_config = release_config["packages"]["."]
    assert package_config["changelog-path"] == "CHANGELOG.md"
    assert {
        extra_file["path"]: extra_file["jsonpath"]
        for extra_file in package_config["extra-files"]
    } == {"custom_components/xray_api/manifest.json": "$.version"}


def test_validation_skips_release_please_technical_branches() -> None:
    """Keep generated Release Please branches out of repository validation."""
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )

    for branch in ("release-please--branches--main",):
        assert workflow.count(f"github.ref_name == '{branch}'") == 2
        assert workflow.count(f"github.head_ref == '{branch}'") == 2

    assert workflow.count("github.event_name == 'push'") == 2
    assert workflow.count("github.event_name == 'pull_request'") == 2
    assert workflow.count(
        "github.event.pull_request.head.repo.full_name == github.repository"
    ) == 2
    assert workflow.count("${{ !(") == 2


def test_validation_pushes_only_stable_branch() -> None:
    """Avoid validation runs for feature branches and tags."""
    workflow = (ROOT / ".github" / "workflows" / "validate.yml").read_text(
        encoding="utf-8"
    )

    assert "  push:\n    branches:\n      - main\n" in workflow
    assert "  pull_request:\n" in workflow
    assert "  schedule:\n" not in workflow
    assert "  workflow_dispatch:\n" in workflow


def test_release_workflows_use_stable_release_please_and_manual_beta() -> None:
    """Keep stable automation and manual beta publishing separate."""
    beta_workflow = (ROOT / ".github" / "workflows" / "beta-release.yml").read_text(
        encoding="utf-8"
    )
    stable_workflow = (ROOT / ".github" / "workflows" / "release-please.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in beta_workflow
    assert "source_ref:" in beta_workflow
    assert "github.workflow_ref" in beta_workflow
    assert "@refs/heads/main" in beta_workflow
    assert "Run this workflow from the main branch" in beta_workflow
    assert "persist-credentials: false" in beta_workflow
    assert "uses: ietf-tools/semver-action@v1.11.0" in beta_workflow
    assert "branch: ${{ steps.source.outputs.sha }}" in beta_workflow
    assert "id: manifest" in beta_workflow
    assert "jq -r '.version // empty'" in beta_workflow
    assert "fromTag: ${{ steps.manifest.outputs.version }}" in beta_workflow
    assert "minorList: feat" in beta_workflow
    assert "patchList: fix" in beta_workflow
    assert "github.run_started_at" in beta_workflow
    assert "date -u -d" in beta_workflow
    assert "tag=${{ steps.version.outputs.nextStrict }}-beta." in beta_workflow
    assert 'tag=v${{ steps.version.outputs.nextStrict }}-beta.' not in beta_workflow
    assert "uses: ncipollo/release-action@v1.20.0" in beta_workflow
    assert (
        "tag: ${{ steps.beta.outputs.tag }}"
        in beta_workflow
    )
    assert "commit: ${{ steps.source.outputs.sha }}" in beta_workflow
    assert "prerelease: true" in beta_workflow
    assert "id: existing" in beta_workflow
    assert ".prerelease" in beta_workflow
    assert "Existing tag" in beta_workflow
    assert "api_status()" in beta_workflow
    assert "--include --silent" in beta_workflow
    assert "GitHub API returned HTTP" in beta_workflow
    assert "Release $TAG exists but its tag ref is missing" in beta_workflow
    assert "CHANGELOG.md" not in beta_workflow

    assert "      - main\n" in stable_workflow
    assert "target-branch: main" in stable_workflow
    assert "config-file: release-please-config.json" in stable_workflow
    assert "manifest-file: .release-please-manifest.json" in stable_workflow
    assert "secrets.RELEASE_PLEASE_TOKEN" in stable_workflow
