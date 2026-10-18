"""Fixtures for Hungarian transport tests."""

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ from this repository."""
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

