"""The PyPI package, the MCP Registry entry and the plugin agree on name and version.

server.json tells the MCP Registry to run the PyPI package `identifier` at
`version` with uvx. That only works if pyproject.toml builds that exact
package, with a console script, at the same version the bridge reports.
The CI `package` job builds the wheel and runs it; these tests keep the
metadata from drifting apart in the meantime.
"""

import json
import pathlib
import re
import tomllib

import unreal_ai_connection_bridge as bridge

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _server_json():
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def _pypi_package():
    (pkg,) = [p for p in _server_json()["packages"] if p["registryType"] == "pypi"]
    return pkg


def test_pyproject_has_a_build_backend():
    assert _pyproject()["build-system"]["build-backend"] == "hatchling.build"


def test_registry_identifier_is_the_pyproject_name():
    assert _pypi_package()["identifier"] == _pyproject()["project"]["name"]


def test_console_script_matches_the_package_name_and_runs_main():
    project = _pyproject()["project"]
    assert project["scripts"] == {project["name"]: "unreal_ai_connection_bridge:main"}
    assert callable(bridge.main)


def test_version_has_one_source():
    """pyproject reads the version from the bridge; everything else must match it."""
    pp = _pyproject()
    assert "version" in pp["project"]["dynamic"]
    assert "version" not in pp["project"]
    hatch = pp["tool"]["hatch"]["version"]
    src = (ROOT / hatch["path"]).read_text(encoding="utf-8")
    # hatchling applies the pattern with re.MULTILINE.
    assert re.search(hatch["pattern"], src, re.MULTILINE).group("version") == bridge.SERVER_VERSION

    uplugin = json.loads(
        (ROOT / "UnrealAIConnection" / "UnrealAIConnection.uplugin").read_text(encoding="utf-8")
    )
    assert uplugin["VersionName"] == bridge.SERVER_VERSION
    assert _server_json()["version"] == bridge.SERVER_VERSION
    assert _pypi_package()["version"] == bridge.SERVER_VERSION


def test_wheel_ships_only_the_bridge_module():
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["only-include"] == ["bridge/unreal_ai_connection_bridge.py"]
    assert wheel["sources"] == ["bridge"]


def test_no_license_classifier_next_to_a_license_expression():
    """PEP 639: setuptools/hatchling reject `License ::` classifiers when
    `license` is an SPDX expression; that is what broke `pip wheel .`."""
    project = _pyproject()["project"]
    assert project["license"] == "MIT"
    assert not [c for c in project.get("classifiers", []) if c.startswith("License ::")]


def test_registry_description_fits_the_schema_limit():
    """The 2025-12-11 server.schema.json caps `description` at 100 characters;
    `mcp-publisher publish` rejects anything longer."""
    desc = _server_json()["description"]
    assert 1 <= len(desc) <= 100, f"{len(desc)} chars: {desc!r}"


def test_readme_carries_the_registry_ownership_marker():
    """The MCP Registry verifies a PyPI package by finding `mcp-name: <server name>`
    in its README (the package's long description)."""
    readme = (ROOT / _pyproject()["project"]["readme"]).read_text(encoding="utf-8")
    assert f"mcp-name: {_server_json()['name']}" in readme
