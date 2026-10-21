"""Fixtures for Hungarian transport tests."""

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ from this repository."""
    yield


@pytest.fixture(autouse=True)
def no_http_server():
    """Set up http without listening on a socket. The harness does this
    itself from Home Assistant 2025.x on; the oldest supported release
    (hacs.json, CI job "Integration tests (oldest HA)") needs it here."""
    with patch("homeassistant.components.http.start_http_server_and_save_config"):
        yield


@pytest.fixture(autouse=True)
def card_assets(tmp_path, monkeypatch):
    """Release zips ship www/; the source tree does not."""
    www = tmp_path / "www"
    www.mkdir()
    (www / "hungarian-transport.js").write_text("// card", encoding="utf-8")
    (www / "hungarian-transport-loader.js").write_text("// loader", encoding="utf-8")
    monkeypatch.setattr(
        "custom_components.hungarian_transport.WWW_PATH", www
    )
    return www


@pytest.fixture(autouse=True)
def mav_source(tmp_path, monkeypatch):
    """Keep the MÁV feed out of the shared testing_config directory."""
    src = tmp_path / "config" / "hungarian-transport" / "mav-gtfs.zip"
    monkeypatch.setattr(
        "custom_components.hungarian_transport.mav.source_path", lambda hass: src
    )
    return src

