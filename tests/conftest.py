"""Fixtures for Hungarian transport tests."""

import base64
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


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



@pytest.fixture(autouse=True)
def no_daily_mav_index(monkeypatch):
    """No test reaches GitHub Pages for the daily MÁV index unless it asks
    (tests/test_mav_daily.py overrides this fixture)."""

    from custom_components.hungarian_transport import mav_daily

    async def _no_update(hass, www):
        return mav_daily.DailyRound(mav_daily.UP_TO_DATE)

    monkeypatch.setattr(
        "custom_components.hungarian_transport.mav_daily.async_update", _no_update
    )


@pytest.fixture(autouse=True)
def no_pages_timetables(monkeypatch):
    """No test reaches GitHub Pages for the weekly Volán and city indexes
    unless it asks (tests/test_weekly_timetables.py overrides this fixture)."""

    from custom_components.hungarian_transport import weekly_timetables

    async def _no_update(hass, www):
        return weekly_timetables.TimetablesRound()

    monkeypatch.setattr(
        "custom_components.hungarian_transport.weekly_timetables.async_update", _no_update
    )


# The key the tests sign the GitHub Pages manifests with, unless a test
# module overrides pages_key with its own.
_PAGES_KEY = Ed25519PrivateKey.generate()


def pages_public(key: Ed25519PrivateKey) -> str:
    """key's public half as PAGES_PUBLIC_KEYS lists one (raw, base64)."""
    return base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


@pytest.fixture
def pages_key() -> Ed25519PrivateKey:
    """The key the Pages manifests (mav-index.json, timetables.json) are
    signed with in a test: the integration checks with its public half."""
    return _PAGES_KEY


@pytest.fixture
def real_signature(monkeypatch, pages_key):
    """The manifests' signatures are fetched and checked for real: the test
    serves each .sig itself (signed with pages_key, or not)."""
    from custom_components.hungarian_transport import mav_daily

    monkeypatch.setattr(mav_daily, "PAGES_PUBLIC_KEYS", (pages_public(pages_key),))


@pytest.fixture(autouse=True)
def signed_manifest(request, monkeypatch):
    """Most tests are about what a manifest names, not its signature. Unless
    a test asks for real_signature, only the HTTP answer for a .sig is
    stubbed: mav-index.json.sig and timetables.json.sig answer pages_key's
    signature of the bytes their manifest's last download returned (not
    through aioclient_mock, so no test counts that request), and the check
    itself (mav_daily.check_signature, verify_signature, the settling) runs
    as it does."""
    if "real_signature" in request.fixturenames:
        return
    from custom_components.hungarian_transport import mav_daily, weekly_timetables

    key = request.getfixturevalue("pages_key")
    monkeypatch.setattr(mav_daily, "PAGES_PUBLIC_KEYS", (pages_public(key),))
    real_get = mav_daily._get
    manifests = {mav_daily.MANIFEST_URL, weekly_timetables.MANIFEST_URL}
    bodies: dict[str, bytes] = {}

    async def get(session, url, limit, timeout, sink=None):
        if url.endswith(".sig") and url.removesuffix(".sig") in bodies:
            return base64.b64encode(key.sign(bodies[url.removesuffix(".sig")])) + b"\n"
        body = await real_get(session, url, limit, timeout, sink)
        if url in manifests:
            bodies[url] = body
        return body

    monkeypatch.setattr(mav_daily, "_get", get)
