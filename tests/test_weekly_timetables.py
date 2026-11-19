"""The weekly Volán and city indexes from GitHub Pages (weekly_timetables.py)."""

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

import pytest
from awesomeversion import AwesomeVersion
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import (
    mav_daily,
    served_index,
    storage,
    timetable_notify,
    timetables,
    weekly_timetables as wt,
)
from custom_components.hungarian_transport.const import (
    CONF_DAILY_MAV,
    CONF_NOTIFY_UPDATES,
    CONF_PAGES_TIMETABLES,
    DOMAIN,
    INDEX_SCHEMA,
)

ROOT = Path(__file__).resolve().parents[1]
VOLAN_OLD = "volan:01716.20261026"
VOLAN_NEW = "volan:01716.20261102"
CITY_OLD = "bkk:20261103;miskolc:1.3;szeged:20261020"
CITY_NEW = "bkk:20261110;miskolc:1.3;szeged:20261020"
SHARDS = 4

# The real round, before conftest's no_pages_timetables replaces it.
_real_update = wt.async_update
# The keys the integration ships (the current one first), before test_key
# replaces them.
_SHIPPED_KEYS = mav_daily.PAGES_PUBLIC_KEYS
_SHIPPED_KEY = _SHIPPED_KEYS[0]
# The tests' signing key: the Site signs its manifest with it, and the
# integration checks with its public half (test_key).
_KEY = Ed25519PrivateKey.generate()
_PUBLIC = base64.b64encode(_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


@pytest.fixture
def no_pages_timetables():
    """Overrides the conftest fixture: these tests run the real rounds."""
    yield


@pytest.fixture(autouse=True)
def test_key(monkeypatch):
    monkeypatch.setattr(mav_daily, "PAGES_PUBLIC_KEYS", (_PUBLIC,))


def _sign(body: bytes, key: Ed25519PrivateKey = _KEY) -> str:  # gitleaks:allow (a test signing helper, no secret)
    """timetables.json.sig as the workflow writes it: one line of base64."""
    return base64.b64encode(key.sign(body)).decode() + "\n"


def _serve_manifest(aioclient_mock, body: bytes, sig: str | dict | None = None) -> None:
    """timetables.json with body and its signature: by default a good one,
    else the text sig, or the response sig (a dict) describes."""
    aioclient_mock.get(wt.MANIFEST_URL, content=body)
    if isinstance(sig, dict):
        aioclient_mock.get(wt.SIGNATURE_URL, **sig)
    else:
        aioclient_mock.get(wt.SIGNATURE_URL, text=_sign(body) if sig is None else sig)


def _shards(tag: str) -> dict[str, bytes]:
    return {
        f"{k:02d}.json.gz": gzip.compress(json.dumps({"v": 1, "p": [f"{tag}{k}"]}).encode(), mtime=0)
        for k in range(SHARDS)
    }


def _folder(stem: str, tag: str) -> str:
    return f"{stem}-shapes-" + hashlib.sha1(tag.encode()).hexdigest()[:10]


def _index(stem: str, feed: str, folder: str | None, schema: int = INDEX_SCHEMA) -> bytes:
    """An index head as scripts/gtfs_compact.write_index writes it."""
    idx: dict = {"v": schema}
    if folder:
        idx["sh"] = {"dir": folder, "n": SHARDS}
    idx.update({"feed": feed, "s": [["a", "Stop", "1"]], "c": [], "a": [], "r": [], "t": [[0, "1", [0], [0]]]})
    if stem == "city":
        idx["o"] = [[op.split(":")[0], "X"] for op in feed.split(";")]
    raw = json.dumps(idx, ensure_ascii=False, separators=(",", ":")).encode()
    return gzip.compress(raw, 9, mtime=0)


class Site:
    """What GitHub Pages serves: timetables.json, the indexes and their shards."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.manifest: dict = {"format": 1, "schema": INDEX_SCHEMA, "built_at": "2026-11-16T10:40:00Z",
                               "commit": "0" * 40, "indexes": {}}

    def publish(self, name: str, feed: str, tag: str | None = None, **over) -> tuple[bytes, str | None, dict]:
        stem = name
        tag = tag or feed
        folder = _folder(stem, tag)
        shards = _shards(tag)
        raw = _index(stem, feed, folder)
        self.files[f"{stem}-index.json.gz"] = raw
        for shard, body in shards.items():
            self.files[f"{folder}/{shard}"] = body
        entry = {
            "url": f"{stem}-index.json.gz",
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "feed": feed,
            "built_at": "2026-11-16T10:40:00Z",
            "trips": 1,
            "stops": 1,
            "shapes": {
                "dir": folder,
                "n": SHARDS,
                "files": {s: {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for s, b in shards.items()},
            },
        }
        entry.update(over)
        self.manifest["indexes"][name] = entry
        return raw, folder, shards

    def body(self) -> bytes:
        return json.dumps(self.manifest, separators=(",", ":")).encode()

    def serve(self, aioclient_mock, sig: str | None = None) -> None:
        _serve_manifest(aioclient_mock, self.body(), sig)
        for path, body in self.files.items():
            aioclient_mock.get(f"{mav_daily.PAGES_BASE}/{path}", content=body)


def _install_local(www: Path, stem: str, feed: str, tag: str | None = None) -> tuple[bytes, str]:
    """An index (and its shapes) as a release or the updater put it in a folder."""
    tag = tag or f"local-{feed}"
    folder = _folder(stem, tag)
    raw = _index(stem, feed, folder)
    (www / f"{stem}-index.json.gz").write_bytes(raw)
    (www / folder).mkdir(parents=True, exist_ok=True)
    for shard, body in _shards(tag).items():
        (www / folder / shard).write_bytes(body)
    return raw, folder


def _flipped(raw: bytes) -> bytes:
    """raw with one byte changed: of the same size, another sha256."""
    mid = len(raw) // 2
    return raw[:mid] + bytes([raw[mid] ^ 0xFF]) + raw[mid + 1:]


def _snapshot(www: Path) -> dict[str, bytes]:
    """What www/ serves: every file but the staging folders'."""
    return {
        str(p.relative_to(www)): p.read_bytes() for p in sorted(www.rglob("*"))
        if p.is_file() and not p.relative_to(www).parts[0].startswith(wt.STAGING)
    }


def _staged(www: Path) -> list[Path]:
    return [p for p in www.iterdir() if p.name.startswith(wt.STAGING)]


def _requested(aioclient_mock) -> list[str]:
    return [str(call[1]) for call in aioclient_mock.mock_calls]


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == wt.__name__ and r.levelno >= logging.WARNING]


async def _until(condition, ticks: int = 1000) -> None:
    """Let the loop run until condition() holds (at most ticks passes)."""
    for _ in range(ticks):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never held")


async def _round(hass: HomeAssistant, www: Path) -> dict[str, str]:
    done = await wt.async_update(hass, www)
    await hass.async_block_till_done()
    return {name: res.result for name, res in done.results.items()}


# --- the schema the cards read ---------------------------------------------


def test_index_schema_matches_the_cards_and_the_builders() -> None:
    tags = (ROOT / "src" / "tags.js").read_text(encoding="utf-8")
    compact = (ROOT / "scripts" / "gtfs_compact.py").read_text(encoding="utf-8")
    assert re.search(r"^export const INDEX_SCHEMA = (\d+);", tags, re.M).group(1) == str(INDEX_SCHEMA)
    assert re.search(r"^INDEX_SCHEMA = (\d+)$", compact, re.M).group(1) == str(INDEX_SCHEMA)


def test_the_manifest_url_is_on_the_fixed_pages_site() -> None:
    assert wt.MANIFEST_URL == "https://bator.github.io/hungarian-transport/timetables.json"


# --- the manifest ------------------------------------------------------------


@pytest.mark.parametrize("over", [{"format": 2}, {"schema": INDEX_SCHEMA + 1}, {"schema": INDEX_SCHEMA - 1}])
async def test_another_format_or_schema_is_unsupported_and_installs_nothing(
    hass: HomeAssistant, aioclient_mock, card_assets, caplog, over
) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.manifest.update(over)
    site.serve(aioclient_mock)
    caplog.set_level(logging.INFO)
    before = _snapshot(card_assets)
    for _ in range(2):
        assert await _round(hass, card_assets) == {"volan": wt.UNSUPPORTED, "city": wt.UNSUPPORTED}
    assert _requested(aioclient_mock) == [wt.MANIFEST_URL, wt.SIGNATURE_URL] * 2
    assert _snapshot(card_assets) == before
    # Said once, as information: an update of the integration reads it.
    assert caplog.text.count("Weekly timetables from GitHub Pages skipped") == 1
    assert _warnings(caplog) == []


async def test_a_broken_manifest_fails_and_is_logged_once(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    _serve_manifest(aioclient_mock, b"{not json")
    for _ in range(2):
        assert await _round(hass, card_assets) == {"volan": wt.FAILED, "city": wt.FAILED}
    assert len(_warnings(caplog)) == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda e: e.update(feed="volan:undated"), "no dated feed"),
        (lambda e: e.update(size=wt.MAX_INDEX + 1), "out of range"),
        (lambda e: e.update(sha256="x"), "no sha256"),
        (lambda e: e["shapes"].update(dir="city-shapes-0123456789"), "not one of volan"),
        (lambda e: e["shapes"].update(dir="volan-shapes-0123456789.tmp"), "not one of volan"),
        (lambda e: e["shapes"]["files"].pop("03.json.gz"), "other shards"),
        (lambda e: e["shapes"]["files"]["00.json.gz"].update(size=wt.MAX_SHARD + 1), "no size or sha256"),
    ],
)
def test_a_bad_index_entry_is_refused(change, message) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    entry = site.manifest["indexes"]["volan"]
    change(entry)
    with pytest.raises(mav_daily.DailyIndexError, match=message):
        wt.check_entry("volan", entry)


def test_an_index_and_its_shapes_are_capped_together(monkeypatch) -> None:
    site = Site()
    raw, _, _ = site.publish("volan", VOLAN_NEW)
    monkeypatch.setattr(wt, "MAX_TOTAL", len(raw) + 10)
    with pytest.raises(mav_daily.DailyIndexError, match="more than"):
        wt.check_entry("volan", site.manifest["indexes"]["volan"])


def test_the_index_must_be_what_its_entry_describes() -> None:
    site = Site()
    raw, folder, _ = site.publish("volan", VOLAN_NEW)
    entry = wt.check_entry("volan", site.manifest["indexes"]["volan"])
    wt.check_index(raw, entry)

    def entry_for(body: bytes) -> dict:
        return {**entry, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    for body, message in (
        (raw + gzip.compress(b"{}"), "data after its gzip stream"),
        (raw[:-12], "truncated"),
        (_index("volan", VOLAN_OLD, folder), "is of feed"),
        (_index("volan", VOLAN_NEW, None), "names shapes"),
        (_index("volan", VOLAN_NEW, folder, schema=28), "index schema 28"),
        (gzip.compress(b'{"feed":"x"}'), "does not start"),
    ):
        with pytest.raises(mav_daily.DailyIndexError, match=message):
            wt.check_index(body, entry_for(body))
    with pytest.raises(wt._Mismatch):
        wt.check_index(_flipped(raw), entry)


# --- what installs -----------------------------------------------------------


async def test_a_newer_volan_feed_installs_with_its_shapes(
    hass: HomeAssistant, aioclient_mock, card_assets, caplog
) -> None:
    _, old_folder = _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    raw, folder, shards = site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    caplog.set_level(logging.INFO)

    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    out = card_assets / timetables.VOLAN_INDEX
    assert out.read_bytes() == raw and out.stat().st_mode & 0o777 == 0o644
    assert {p.name: p.read_bytes() for p in (card_assets / folder).iterdir()} == shards
    assert (card_assets / folder).stat().st_mode & 0o777 == 0o755
    # The previous folder stays for the dashboards still on the old index.
    assert (card_assets / old_folder / wt.SHAPES_RETIRED).is_file()
    assert not [p for p in card_assets.iterdir() if p.name.startswith(wt.STAGING) or ".json.gz." in p.name]
    assert f"Installed the weekly volan timetable of feed {VOLAN_NEW}" in caplog.text
    record = (await storage.async_load(hass))[storage.TIMETABLES_INSTALL]["volan"]
    assert record["feed"] == VOLAN_NEW and record["size"] == len(raw)
    assert record["sha256"] == hashlib.sha256(raw).hexdigest()
    # The city index is not on the site: nothing asked for it.
    assert not [u for u in _requested(aioclient_mock) if "city" in u]


async def test_a_missing_index_installs(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    site = Site()
    raw, _, _ = site.publish("city", CITY_OLD)
    site.serve(aioclient_mock)
    assert await _round(hass, card_assets) == {"volan": wt.NOT_PUBLISHED, "city": wt.INSTALLED}
    assert (card_assets / timetables.CITY_INDEX).read_bytes() == raw


@pytest.mark.parametrize(
    ("installed", "result"),
    [(VOLAN_NEW, wt.UP_TO_DATE), ("volan:01716.20261109", wt.SUPERSEDED), ("volan:01716.20261102b", wt.SUPERSEDED)],
)
async def test_the_same_or_an_older_volan_feed_is_not_downloaded(
    hass: HomeAssistant, aioclient_mock, card_assets, installed, result
) -> None:
    _install_local(card_assets, "volan", installed)
    before = _snapshot(card_assets)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == result
    assert _requested(aioclient_mock) == [wt.MANIFEST_URL, wt.SIGNATURE_URL]
    assert _snapshot(card_assets) == before


@pytest.mark.parametrize(
    ("installed", "pages", "install"),
    [
        # An operator's later feed.
        (CITY_OLD, CITY_NEW, True),
        # The same feeds.
        (CITY_NEW, CITY_NEW, False),
        # One later, one older: an older timetable is never installed.
        (CITY_OLD, "bkk:20261110;miskolc:1.3;szeged:20261013", False),
        # An operator more, or one fewer.
        (CITY_OLD, CITY_OLD + ";pecs:20261101", True),
        (CITY_OLD, "bkk:20261103;miskolc:1.3", True),
        # An undated feed that changed: cannot be ordered, so it counts.
        (CITY_OLD, "bkk:20261103;miskolc:1.4;szeged:20261020", True),
        # A newer operator does not make up for an older one.
        (CITY_OLD, "bkk:20261027;miskolc:1.3;szeged:20261020;pecs:20261101", False),
    ],
)
def test_city_decisions_compare_operator_by_operator(installed, pages, install) -> None:
    assert wt.city_newer(pages, installed) is install


@pytest.mark.parametrize(
    ("durable", "result"),
    [
        # The local updater rebuilt a later feed: it stays.
        ("volan:01716.20261109", wt.LOCAL_WINS),
        # Its copy is older than Pages': installed (the updater keeps it then).
        (VOLAN_OLD, wt.INSTALLED),
    ],
)
async def test_a_local_rebuild_with_newer_feeds_is_not_overwritten(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source, durable, result
) -> None:
    base = mav_source.parent
    base.mkdir(parents=True)
    _install_local(card_assets, "volan", durable)
    _install_local(base, "volan", durable)
    before = _snapshot(card_assets)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == result
    if result == wt.LOCAL_WINS:
        assert _snapshot(card_assets) == before
        assert _requested(aioclient_mock) == [wt.MANIFEST_URL, wt.SIGNATURE_URL]


async def test_a_city_copy_the_updater_would_restore_wins(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source
) -> None:
    """Pages' city index has an operator fewer than the updater's copy: the
    updater (served_feeds_newer) would put its copy back, so it stays."""
    base = mav_source.parent
    base.mkdir(parents=True)
    _install_local(card_assets, "city", CITY_OLD)
    _install_local(base, "city", CITY_OLD)
    site = Site()
    site.publish("city", "bkk:20261110;miskolc:1.3")
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["city"] == wt.LOCAL_WINS


def test_the_updater_rule_is_the_update_script_s(tmp_path) -> None:
    """updater_newer is scripts/update_volan_index.served_feeds_newer on feeds."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("uvi", ROOT / "scripts" / "update_volan_index.py")
    uvi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(uvi)
    cases = [
        (CITY_OLD, CITY_NEW), (CITY_NEW, CITY_OLD), (CITY_OLD, CITY_OLD), (CITY_OLD, CITY_OLD + ";pecs:20261101"),
        (CITY_OLD, "bkk:20261110;miskolc:1.4;szeged:20261020"), (CITY_OLD, "bkk:20261110;szeged:20261020"),
        (VOLAN_OLD, VOLAN_NEW), (VOLAN_NEW, VOLAN_OLD), ("volan:x", "volan:20261102"),
    ]
    for old, new in cases:
        durable, served = tmp_path / "d.json.gz", tmp_path / "s.json.gz"
        durable.write_bytes(_index("city", old, None))
        served.write_bytes(_index("city", new, None))
        assert wt.updater_newer(new, old) is uvi.served_feeds_newer(durable, served), (old, new)


async def test_a_broken_served_index_is_replaced(hass: HomeAssistant, aioclient_mock, card_assets, mav_source) -> None:
    base = mav_source.parent
    base.mkdir(parents=True)
    _install_local(base, "volan", "volan:01716.20261109")
    (card_assets / timetables.VOLAN_INDEX).write_bytes(b"not gzip")
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED


# --- downloads that do not check out -----------------------------------------


async def test_a_bad_shard_leaves_www_untouched(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    _install_local(card_assets, "volan", VOLAN_OLD)
    before = _snapshot(card_assets)
    site = Site()
    _, folder, _ = site.publish("volan", VOLAN_NEW)
    site.files[f"{folder}/02.json.gz"] = b"x" * len(site.files[f"{folder}/02.json.gz"])
    site.serve(aioclient_mock)
    # The first mismatch may be the Pages cache; the second is not.
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    assert (await _round(hass, card_assets))["volan"] == wt.FAILED
    assert _snapshot(card_assets) == before
    # The staging folder keeps only the shards that checked out (a retry
    # downloads the rest), never the bad one.
    for staging in _staged(card_assets):
        assert "02.json.gz" not in {p.name for p in staging.iterdir()}
    assert [w for w in _warnings(caplog) if "sha256 does not match" in w]


async def test_a_missing_shard_fails_and_leaves_www_untouched(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    _install_local(card_assets, "volan", VOLAN_OLD)
    before = _snapshot(card_assets)
    site = Site()
    _, folder, _ = site.publish("volan", VOLAN_NEW)
    body = site.files.pop(f"{folder}/01.json.gz")
    site.serve(aioclient_mock)
    aioclient_mock.get(f"{mav_daily.PAGES_BASE}/{folder}/01.json.gz", status=404, content=body)
    # A 404 of a file the manifest names may be a cached manifest of the
    # previous deploy: looked at again, then FAILED when it stays so.
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    assert (await _round(hass, card_assets))["volan"] == wt.FAILED
    assert _snapshot(card_assets) == before
    assert not [p for p in card_assets.iterdir() if "." in p.name.split("-shapes-")[-1] and p.is_dir()]


async def test_an_index_of_another_generation_settles_then_fails(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    """Right after a deploy the cache can pair the manifest with the old
    index: no round the first time, FAILED when it stays so."""
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.files["volan-index.json.gz"] = _index("volan", VOLAN_OLD, None) + b"pad"
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    assert (await _round(hass, card_assets))["volan"] == wt.FAILED
    assert not (card_assets / timetables.VOLAN_INDEX).exists()


async def test_a_longer_download_than_the_manifest_says_settles(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    site = Site()
    raw, _, _ = site.publish("volan", VOLAN_NEW)
    site.files["volan-index.json.gz"] = raw + b"\0" * 100
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING


async def test_a_redirect_is_not_followed(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    aioclient_mock.get(wt.MANIFEST_URL, status=301, headers={"Location": "https://example.org/timetables.json"})
    assert await _round(hass, card_assets) == {"volan": wt.FAILED, "city": wt.FAILED}
    assert "redirects are not followed" in caplog.text
    assert _requested(aioclient_mock) == [wt.MANIFEST_URL]


# --- the shapes folders -------------------------------------------------------


def test_shapes_folders_are_retired_then_removed_after_the_grace(tmp_path) -> None:
    www = tmp_path
    _, keep = _install_local(www, "volan", VOLAN_NEW)
    old = www / _folder("volan", "old")
    old.mkdir()
    city = www / _folder("city", "c")
    city.mkdir()
    temp = www / (_folder("volan", "x") + ".tmpabcd1234")
    temp.mkdir()
    stray = www / (timetables.VOLAN_INDEX + ".abcd1234")
    stray.write_bytes(b"x")
    staging = www / f"{wt.STAGING}{_folder('volan', 'y')}-abcd1234"
    staging.mkdir()
    now = 2_000_000_000.0
    for path in (temp, stray, staging):
        os.utime(path, (now - 10, now - 10))

    wt.prune(www, "volan", keep, now=now)
    assert (old / wt.SHAPES_RETIRED).is_file() and not (www / keep / wt.SHAPES_RETIRED).exists()
    # Another stem, and young temp files (another writer's), are left alone.
    assert not (city / wt.SHAPES_RETIRED).exists() and temp.is_dir() and stray.is_file() and staging.is_dir()

    wt.prune(www, "volan", keep, now=now + wt.SHAPES_GRACE - 1)
    assert old.is_dir()
    wt.prune(www, "volan", keep, now=now + wt.SHAPES_GRACE)
    assert not old.exists() and not temp.exists() and not stray.exists() and not staging.exists()
    assert (www / keep).is_dir() and city.is_dir()


def test_a_folder_in_use_again_loses_its_mark(tmp_path) -> None:
    _, keep = _install_local(tmp_path, "volan", VOLAN_NEW)
    (tmp_path / keep / wt.SHAPES_RETIRED).touch()
    wt.prune(tmp_path, "volan", keep, now=2_000_000_000.0)
    assert not (tmp_path / keep / wt.SHAPES_RETIRED).exists()


async def test_an_install_swaps_the_folder_first_then_the_index_and_retires_the_old(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    _, old_folder = _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    raw, folder, _ = site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    order: list[str] = []
    real_replace = wt._replace
    real_write = wt.mav_index.write_bytes

    def replace(src, dst):
        if Path(src).is_dir():
            order.append(f"folder {Path(dst).name}")
        return real_replace(src, dst)

    def write(data, out, suffix=None):
        assert (card_assets / folder).is_dir()
        order.append(f"index {Path(out).name}")
        return real_write(data, out, suffix)

    monkeypatch.setattr(wt, "_replace", replace)
    monkeypatch.setattr(wt.mav_index, "write_bytes", write)
    now = 2_000_000_000.0
    monkeypatch.setattr(wt.time_mod, "time", lambda: now)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert order == [f"folder {folder}", f"index {timetables.VOLAN_INDEX}"]
    assert (card_assets / old_folder / wt.SHAPES_RETIRED).stat().st_mtime == now

    # The next rounds prune too: the old folder goes once the grace is over.
    now += wt.SHAPES_GRACE - 60
    assert (await _round(hass, card_assets))["volan"] == wt.UP_TO_DATE
    assert (card_assets / old_folder).is_dir()
    now += 60
    assert (await _round(hass, card_assets))["volan"] == wt.UP_TO_DATE
    assert not (card_assets / old_folder).exists() and (card_assets / folder).is_dir()


async def test_a_shapes_folder_already_in_place_is_not_downloaded_again(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    """A new index on the old shapes (only the timetable changed)."""
    _, folder = _install_local(card_assets, "volan", VOLAN_OLD, tag="same")
    site = Site()
    site.publish("volan", VOLAN_NEW, tag="same")
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert not [u for u in _requested(aioclient_mock) if "-shapes-" in u]
    assert not (card_assets / folder / wt.SHAPES_RETIRED).exists()


async def test_a_broken_folder_of_the_same_name_is_replaced(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    _, folder = _install_local(card_assets, "volan", VOLAN_OLD, tag="same")
    (card_assets / folder / "01.json.gz").write_bytes(b"broken")
    site = Site()
    _, _, shards = site.publish("volan", VOLAN_NEW, tag="same")
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert {p.name: p.read_bytes() for p in (card_assets / folder).iterdir()} == shards
    assert not [p for p in card_assets.iterdir() if p.name.startswith(wt.STAGING)]


async def test_shards_download_at_most_concurrency_at_a_time(hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch) -> None:
    running = peak = 0
    real = wt._download_shard

    async def counted(*args):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        try:
            await real(*args)
        finally:
            running -= 1

    monkeypatch.setattr(wt, "_download_shard", counted)
    monkeypatch.setattr(wt, "CONCURRENCY", 2)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert peak == 2
    assert wt.CONCURRENCY <= 4


# --- the rounds in Home Assistant --------------------------------------------


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    await hass.config.async_set_time_zone("UTC")
    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def _at(hass: HomeAssistant, freezer, when: str) -> None:
    moment = dt_util.parse_datetime(when)
    freezer.move_to(moment)
    async_fire_time_changed(hass, moment)
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.fixture
def morning(freezer, monkeypatch):
    """03:00 UTC on 16 November 2026; every random delay at its upper end."""
    freezer.move_to("2026-11-16 03:00:00+00:00")
    monkeypatch.setattr(mav_daily.random, "uniform", lambda a, b: b)


def _manifest_requests(aioclient_mock) -> int:
    return _requested(aioclient_mock).count(wt.MANIFEST_URL)


async def test_runs_after_the_mav_part_of_the_first_and_daily_rounds(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    order: list[str] = []

    async def mav_round(hass, www):
        order.append("mav")
        return mav_daily.DailyRound(mav_daily.UP_TO_DATE)

    async def pages_round(hass, www):
        order.append("pages")
        return await _real_update(hass, www)

    monkeypatch.setattr(mav_daily, "async_update", mav_round)
    monkeypatch.setattr(wt, "async_update", pages_round)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    entry = await _setup(hass)
    await _at(hass, freezer, "2026-11-16 03:19:59+00:00")
    assert order == []
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    assert order == ["mav", "pages"]
    assert (card_assets / timetables.VOLAN_INDEX).is_file()
    await _at(hass, freezer, "2026-11-16 06:00:00+00:00")
    await _at(hass, freezer, "2026-11-16 06:05:01+00:00")
    assert order == ["mav", "pages", "mav", "pages"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await _at(hass, freezer, "2026-11-17 06:10:00+00:00")
    assert order == ["mav", "pages", "mav", "pages"]


async def test_runs_with_the_daily_mav_index_off(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    mav_rounds = []

    async def mav_round(hass, www):
        mav_rounds.append(www)
        return mav_daily.DailyRound(mav_daily.UP_TO_DATE)

    monkeypatch.setattr(mav_daily, "async_update", mav_round)
    monkeypatch.setattr(wt, "async_update", _real_update)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    await _setup(hass, {CONF_DAILY_MAV: False})
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    assert _manifest_requests(aioclient_mock) == 1 and mav_rounds == []
    await _at(hass, freezer, "2026-11-16 06:00:00+00:00")
    await _at(hass, freezer, "2026-11-16 06:05:01+00:00")
    assert _manifest_requests(aioclient_mock) == 2 and mav_rounds == []


async def test_turned_off_makes_no_request(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    await _setup(hass, {CONF_PAGES_TIMETABLES: False})
    for when in ("2026-11-16 03:30:00+00:00", "2026-11-16 06:10:00+00:00", "2026-11-17 06:10:00+00:00"):
        await _at(hass, freezer, when)
    assert aioclient_mock.call_count == 0
    assert not (card_assets / timetables.VOLAN_INDEX).exists()


async def test_a_failed_round_is_retried_and_a_settling_one_looked_at_again(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)
    aioclient_mock.get(wt.MANIFEST_URL, status=500)
    await _setup(hass)
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    assert _manifest_requests(aioclient_mock) == 1
    # RETRY_DELAY apart, RETRY_LIMIT times.
    for n in range(wt.RETRY_LIMIT + 1):
        freezer.tick(wt.RETRY_DELAY + 1)
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done(wait_background_tasks=True)
        assert _manifest_requests(aioclient_mock) == 1 + min(n + 1, wt.RETRY_LIMIT)

    # The daily round, with an index of another generation: looked at
    # again PAGES_SETTLE_DELAY later, quietly, and then installed.
    aioclient_mock.clear_requests()
    site = Site()
    raw, _, _ = site.publish("volan", VOLAN_NEW)
    site.files["volan-index.json.gz"] = _flipped(raw)
    site.serve(aioclient_mock)
    await _at(hass, freezer, "2026-11-16 06:00:00+00:00")
    await _at(hass, freezer, "2026-11-16 06:05:01+00:00")
    assert _manifest_requests(aioclient_mock) == 1
    assert not (card_assets / timetables.VOLAN_INDEX).exists()
    aioclient_mock.clear_requests()
    site.files["volan-index.json.gz"] = raw
    site.serve(aioclient_mock)
    await _at(hass, freezer, "2026-11-16 06:15:00+00:00")
    assert aioclient_mock.call_count == 0
    await _at(hass, freezer, "2026-11-16 06:16:03+00:00")
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == raw


async def test_an_install_notifies_once_and_counts_as_seen_for_the_next_release(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)
    _install_local(card_assets, "volan", VOLAN_OLD)
    _install_local(card_assets, "city", CITY_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.publish("city", CITY_NEW)
    site.serve(aioclient_mock)
    calls = []
    hass.services.async_register("script", "ertesit", lambda call: calls.append(dict(call.data)))
    created = []
    real_create = persistent_notification.async_create

    def create(hass_, message, title=None, notification_id=None):
        created.append((notification_id, title, message))
        real_create(hass_, message, title=title, notification_id=notification_id)

    monkeypatch.setattr(persistent_notification, "async_create", create)
    entry = await _setup(hass, {CONF_NOTIFY_UPDATES: True, "notify_service": "script.ertesit"})
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    pages = [c for c in created if c[0] == timetable_notify.NOTIFICATION_PAGES]
    assert pages == [(
        timetable_notify.NOTIFICATION_PAGES,
        "Hungarian transport: timetables updated",
        "New weekly timetables from GitHub Pages are installed: Volán buses (2026-11-02 timetable),"
        " city lines (newest feed: 2026-11-10).",
    )]
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c["message"] for c in calls] == [pages[0][2]]
    # Nothing new the next day: no second notification.
    await _at(hass, freezer, "2026-11-16 06:05:01+00:00")
    assert len([c for c in created if c[0] == timetable_notify.NOTIFICATION_PAGES]) == 1

    # The installed feeds are seen: a release shipping them is no news.
    state = await storage.async_load(hass)
    assert state["shipped"]["feeds"]["volan"] == VOLAN_NEW
    assert state["shipped"]["feeds"]["city"] == CITY_NEW
    assert await hass.config_entries.async_unload(entry.entry_id)
    notifier = timetable_notify.UpdateNotifier(hass, {CONF_NOTIFY_UPDATES: True})
    assert await notifier.async_check_shipped(card_assets, "9.9.9") == []


async def test_sensors_show_the_pages_source_and_the_last_round(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)
    _install_local(card_assets, "volan", VOLAN_OLD)
    _install_local(card_assets, "city", CITY_NEW)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.publish("city", CITY_NEW)
    site.serve(aioclient_mock)
    await _setup(hass)
    volan = hass.states.get("sensor.hungarian_transport_volan_timetable")
    assert volan.attributes["source"] == "release" and "pages_result" not in volan.attributes
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    volan = hass.states.get("sensor.hungarian_transport_volan_timetable")
    assert volan.state == "2026-11-02"
    assert volan.attributes["source"] == "pages"
    assert volan.attributes["pages_result"] == wt.INSTALLED
    assert volan.attributes["pages_checked_at"] == "2026-11-16T03:20:01+00:00"
    city = hass.states.get("sensor.hungarian_transport_city_timetables")
    assert city.attributes["source"] == "release"
    assert city.attributes["pages_result"] == wt.UP_TO_DATE

    # A release (or the updater) put another file there: not Pages' any more.
    _install_local(card_assets, "volan", "volan:01716.20261109")
    async_fire_time_changed(hass, dt_util.utcnow() + timetables.POLL_INTERVAL)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("sensor.hungarian_transport_volan_timetable").attributes["source"] == "release"


async def test_sensors_say_disabled_when_turned_off(hass: HomeAssistant, card_assets) -> None:
    _install_local(card_assets, "volan", VOLAN_OLD)
    _install_local(card_assets, "city", CITY_OLD)
    await _setup(hass, {CONF_PAGES_TIMETABLES: False})
    for entity_id in ("sensor.hungarian_transport_volan_timetable", "sensor.hungarian_transport_city_timetables"):
        attrs = hass.states.get(entity_id).attributes
        assert attrs["pages_result"] == "disabled" and "pages_checked_at" not in attrs


async def test_a_city_copy_of_the_updater_is_its_source(hass: HomeAssistant, card_assets, mav_source) -> None:
    base = mav_source.parent
    base.mkdir(parents=True)
    raw, _ = _install_local(card_assets, "city", CITY_OLD)
    (base / timetables.CITY_INDEX).write_bytes(raw)
    await _setup(hass)
    assert hass.states.get("sensor.hungarian_transport_city_timetables").attributes["source"] == "local_updater"


@pytest.mark.parametrize("lang", ["en", "hu"])
def test_the_option_and_the_new_states_are_translated(lang) -> None:
    pkg = ROOT / "custom_components" / "hungarian_transport"
    strings = json.loads((pkg / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
    init = strings["options"]["step"]["init"]
    assert init["data"][CONF_PAGES_TIMETABLES] and "bator.github.io" in init["data_description"][CONF_PAGES_TIMETABLES]
    results = {wt.INSTALLED, wt.UP_TO_DATE, wt.LOCAL_WINS, wt.SUPERSEDED, wt.NOT_PUBLISHED, wt.FAILED,
               wt.UNSUPPORTED, "disabled"}
    for key in ("volan_timetable", "city_timetables"):
        attrs = strings["entity"]["sensor"][key]["state_attributes"]
        assert set(attrs["pages_result"]["state"]) == results
        assert attrs["source"]["state"]["pages"]
        assert attrs["pages_checked_at"]["name"]


# --- review fixes -------------------------------------------------------------


def test_the_staging_folder_is_not_one_the_updater_prunes(tmp_path) -> None:
    """scripts/gtfs_compact.prune_shapes removes at once every <stem>-shapes-*
    folder with a dot in its suffix (its own temp folders): the staging
    folder must not look like one."""
    site = Site()
    site.publish("volan", VOLAN_NEW)
    entry = wt.check_entry("volan", site.manifest["indexes"]["volan"])
    tmp, missing = wt._staging(tmp_path, entry["shapes"], wt.staging_key(entry))
    assert tmp.is_dir() and tmp.name.startswith(wt.STAGING) and len(missing) == SHARDS
    assert not list(tmp_path.glob("volan-shapes-*"))


async def test_the_local_updater_pruning_during_a_download_does_not_break_it(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    import sys

    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    import gtfs_compact

    _, old_folder = _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    raw, folder, shards = site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    real = wt._download_shard
    pruned = []

    async def updater_runs(*args):
        if not pruned:
            # The local updater rebuilt the index meanwhile and pruned.
            gtfs_compact.prune_shapes(card_assets / timetables.VOLAN_INDEX, old_folder)
            pruned.append(True)
        await real(*args)

    monkeypatch.setattr(wt, "_download_shard", updater_runs)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert {p.name: p.read_bytes() for p in (card_assets / folder).iterdir()} == shards
    assert "gtfs_compact" in sys.modules


async def test_reinstalling_the_last_install_after_a_hacs_update_is_no_news(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    """HACS puts the release's older index back; the next round installs the
    site's again, records it, and says nothing: it is not new."""
    monkeypatch.setattr(wt, "async_update", _real_update)
    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    raw, _, _ = site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    created = []
    real_create = persistent_notification.async_create

    def create(hass_, message, title=None, notification_id=None):
        created.append(notification_id)
        real_create(hass_, message, title=title, notification_id=notification_id)

    monkeypatch.setattr(persistent_notification, "async_create", create)
    entry = await _setup(hass, {CONF_NOTIFY_UPDATES: True})
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    assert created.count(timetable_notify.NOTIFICATION_PAGES) == 1

    # The HACS update: the release's index, and a restart (a reload).
    assert await hass.config_entries.async_unload(entry.entry_id)
    _install_local(card_assets, "volan", VOLAN_OLD)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    first = (await storage.async_load(hass))[storage.TIMETABLES_INSTALL]["volan"]["installed_at"]
    freezer.tick(60)
    await _at(hass, freezer, "2026-11-16 03:45:00+00:00")
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == raw
    record = (await storage.async_load(hass))[storage.TIMETABLES_INSTALL]["volan"]
    assert record["installed_at"] != first
    assert created.count(timetable_notify.NOTIFICATION_PAGES) == 1
    state = await storage.async_load(hass)
    assert state["shipped"]["feeds"]["volan"] == VOLAN_NEW


async def test_a_slow_weekly_round_never_drops_a_mav_retry(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    """The weekly round runs in its own task: the MÁV updater's retry 20
    minutes later runs while the weekly download is still going."""
    mav_rounds = []
    release = asyncio.Event()
    weekly_rounds = []

    async def mav_round(hass, www):
        mav_rounds.append(dt_util.utcnow())
        return mav_daily.DailyRound(mav_daily.FAILED, error="boom")

    async def slow_round(hass, www):
        weekly_rounds.append(dt_util.utcnow())
        await release.wait()
        return wt.TimetablesRound({"volan": wt.IndexResult(wt.UP_TO_DATE, VOLAN_NEW)})

    async def settle(rounds: int) -> None:
        await hass.async_block_till_done()
        await _until(lambda: len(mav_rounds) == rounds)

    monkeypatch.setattr(mav_daily, "async_update", mav_round)
    monkeypatch.setattr(wt, "async_update", slow_round)
    await _setup(hass)
    for n, when in enumerate(("2026-11-16 03:20:01+00:00", "2026-11-16 03:40:02+00:00", "2026-11-16 04:00:03+00:00")):
        moment = dt_util.parse_datetime(when)
        freezer.move_to(moment)
        async_fire_time_changed(hass, moment)
        await settle(n + 1)
    assert len(mav_rounds) == 3 and len(weekly_rounds) == 1
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    rounds = (await storage.async_load(hass))[storage.PAGES_ROUND]
    assert rounds["volan"]["result"] == wt.UP_TO_DATE


async def test_retired_shapes_are_pruned_at_setup_also_with_the_option_off(
    hass: HomeAssistant, card_assets
) -> None:
    _, keep = _install_local(card_assets, "volan", VOLAN_NEW)
    gone = card_assets / _folder("volan", "gone")
    gone.mkdir()
    (gone / wt.SHAPES_RETIRED).touch()
    os.utime(gone / wt.SHAPES_RETIRED, (1_000_000_000, 1_000_000_000))
    fresh = card_assets / _folder("volan", "fresh")
    fresh.mkdir()
    await _setup(hass, {CONF_PAGES_TIMETABLES: False})
    assert not gone.exists()
    assert (fresh / wt.SHAPES_RETIRED).is_file()
    assert (card_assets / keep).is_dir() and not (card_assets / keep / wt.SHAPES_RETIRED).exists()


# --- review round 6 -------------------------------------------------------------

# A city feed as the live site's (shortened), and what the last two stable
# releases shipped before it was built.
LIVE = "miskolc:1.3;debrecen:20261113-1;volan-szeged:01736.20261116"
RELEASE_21 = "miskolc:1.3;debrecen:20261030-1;volan-szeged:01736.20261102"
RELEASE_22 = "miskolc:1.3;debrecen:20261106-1;volan-szeged:01736.20261109"
TODAY = date(2026, 11, 19)


def _www(tmp_path: Path, feed: str) -> tuple[Path, Path]:
    www, durable = tmp_path / "served", tmp_path / "durable"
    www.mkdir()
    durable.mkdir()
    (www / timetables.CITY_INDEX).write_bytes(_index("city", feed, None))
    return www, durable


# The tag of the stable release the Pages build started from, and versions
# of this integration around it (the release workflow numbers a -rev series
# two patches up: 1.10.26-rev.1 after stable 1.10.24).
BASELINE = "v1.10.24"
OLDER = AwesomeVersion("1.10.22")
SAME = AwesomeVersion("1.10.24")
NEWER = AwesomeVersion("1.10.25")
REV = AwesomeVersion("1.10.26-rev.1")
# What a release newer than the baseline ships that the Pages build knew
# nothing of: an operator it added, an undated feed it updated, one it
# dropped.
ADDED = LIVE.replace("volan-szeged:01736.20261116", "volan-szeged:01736.20261109") + ";szeged:20261115"
UNDATED = RELEASE_22.replace("miskolc:1.3", "miskolc:1.4")
DROPPED = "gone:20261101;" + RELEASE_22


@pytest.mark.parametrize("installed", [ADDED, UNDATED, DROPPED], ids=["added", "undated", "dropped"])
def test_an_older_release_takes_the_pages_build_of_a_newer_baseline(tmp_path, installed) -> None:
    """The reviewers' repro (round 7): a user still on an older release than
    the one the Pages build started from. The build knew a newer index than
    this one: whatever the operator rule takes for news is news (an
    operator the baseline added, an undated feed it changed, one it
    dropped). release_feed alone left these SUPERSEDED for ever."""
    www, durable = _www(tmp_path, installed)
    for running in (OLDER, SAME, None):
        assert wt.decide_why("city", LIVE, www, durable, release_version=BASELINE, running=running,
                             today=TODAY) == (wt._INSTALL, None), running


@pytest.mark.parametrize("running", [NEWER, REV], ids=["newer", "rev"])
@pytest.mark.parametrize("installed", [ADDED, UNDATED, DROPPED], ids=["added", "undated", "dropped"])
def test_a_release_newer_than_the_baseline_takes_only_a_strictly_newer_index(tmp_path, installed, running) -> None:
    """This version (a later stable one, a -rev beta) is newer than the
    release the Pages build started from: its index may hold what the build
    knew nothing of, and the operator rule cannot tell. SUPERSEDED, with why."""
    www, durable = _www(tmp_path, installed)
    assert wt.city_newer(LIVE, installed, TODAY)
    assert wt.decide_why("city", LIVE, www, durable, release_version=BASELINE, running=running,
                         today=TODAY) == (wt.SUPERSEDED, wt.RELEASE_NEWER)
    # Without release_version (a site from before it), the operator rule.
    assert wt.decide("city", LIVE, www, durable, running=running, today=TODAY) == wt._INSTALL


@pytest.mark.parametrize("running", [NEWER, REV], ids=["newer", "rev"])
def test_a_release_newer_than_the_baseline_takes_a_strictly_newer_index(tmp_path, running) -> None:
    """Every operator still there, none older, an undated one unchanged, one
    later: newer whatever the build knew (the updater's rule)."""
    www, durable = _www(tmp_path, RELEASE_22)
    assert wt.decide_why("city", LIVE, www, durable, release_version=BASELINE, running=running,
                         today=TODAY) == (wt._INSTALL, None)
    # A served date past MAX_FEED_AHEAD is no date there either.
    (tmp_path / "far").mkdir()
    www, durable = _www(tmp_path / "far", RELEASE_22.replace("20261106-1", "29991231"))
    assert wt.decide("city", LIVE, www, durable, release_version=BASELINE, running=running,
                     today=TODAY) == wt._INSTALL


@pytest.mark.parametrize("running", [OLDER, NEWER, REV])
def test_a_strictly_newer_volan_index_installs_whatever_the_baseline(tmp_path, running) -> None:
    """The Volán index is one dated feed: newer is newer (the release_feed
    rule blocked even this one)."""
    www, durable = tmp_path / "w", tmp_path / "d"
    www.mkdir()
    durable.mkdir()
    (www / timetables.VOLAN_INDEX).write_bytes(_index("volan", "volan:01716.20261109", None))
    assert wt.decide("volan", "volan:01716.20261116", www, durable, release_version=BASELINE, running=running,
                     today=TODAY) == wt._INSTALL
    assert wt.decide_why("volan", "volan:01716.20261102", www, durable, release_version=BASELINE,
                         running=running, today=TODAY) == (wt.SUPERSEDED, wt.SERVED_NEWER)


def test_the_release_rule_applies_only_to_a_release_s_index(tmp_path) -> None:
    """An index this module installed, or the local updater's copy, is not a
    release's: the operator rule decides as before."""
    www, durable = _www(tmp_path, ADDED)
    size = (www / timetables.CITY_INDEX).stat().st_size
    rule = {"release_version": BASELINE, "running": REV, "today": TODAY}
    assert wt.decide("city", LIVE, www, durable, **rule) == wt.SUPERSEDED
    record = {"feed": ADDED, "size": size, "sha256": "0" * 64}
    assert wt.decide("city", LIVE, www, durable, pages_install=record, **rule) == wt._INSTALL
    # Another file of that feed (a release's) is not that install.
    other = {**record, "size": size + 1}
    assert wt.decide("city", LIVE, www, durable, pages_install=other, **rule) == wt.SUPERSEDED
    # The local updater's copy: its own rule, which is the strict one.
    (durable / timetables.CITY_INDEX).write_bytes((www / timetables.CITY_INDEX).read_bytes())
    assert wt.decide("city", LIVE, www, durable, **rule) == wt.LOCAL_WINS


@pytest.mark.parametrize(
    ("running", "tag", "newer"),
    [
        (NEWER, BASELINE, True),
        (REV, BASELINE, True),
        (SAME, BASELINE, False),
        (OLDER, BASELINE, False),
        (SAME, "1.10.24", False),
        # A -rev before its own stable version (semver): older.
        (AwesomeVersion("1.10.24-rev.3"), BASELINE, False),
        (AwesomeVersion("1.10.24-rev.3"), "v1.10.24-rev.2", True),
        (AwesomeVersion("1.10.30"), "v1.10.4", True),
        # Unknown, or nothing to compare: the operator rule.
        (None, BASELINE, False),
        (REV, None, False),
        (AwesomeVersion("not a version"), BASELINE, False),
        # Home Assistant's own dev builds order after every release.
        (AwesomeVersion("dev"), BASELINE, True),
        ("1.10.25", BASELINE, True),
    ],
)
def test_release_newer_orders_the_versions(running, tag, newer) -> None:
    assert wt.release_newer(running, tag) is newer


async def test_the_running_version_is_the_manifest_s(hass: HomeAssistant) -> None:
    manifest = json.loads((ROOT / "custom_components" / DOMAIN / "manifest.json").read_text(encoding="utf-8"))
    version = await wt._running_version(hass)
    assert version == AwesomeVersion(manifest["version"])


async def test_a_pages_build_of_an_older_baseline_is_superseded_on_the_sensor(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)

    async def running(_hass):
        return REV

    monkeypatch.setattr(wt, "_running_version", running)
    raw, _ = _install_local(card_assets, "city", ADDED)
    site = Site()
    site.publish("city", LIVE, release_feed=RELEASE_22, release_version=BASELINE)
    site.serve(aioclient_mock)
    await _setup(hass)
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    city = hass.states.get("sensor.hungarian_transport_city_timetables")
    assert city.attributes["pages_result"] == wt.SUPERSEDED
    assert city.attributes["pages_reason"] == wt.RELEASE_NEWER
    assert (card_assets / timetables.CITY_INDEX).read_bytes() == raw
    assert not [u for u in _requested(aioclient_mock) if "city-" in u]


async def test_a_user_on_an_older_release_takes_the_pages_build(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)

    async def running(_hass):
        return OLDER

    monkeypatch.setattr(wt, "_running_version", running)
    _install_local(card_assets, "city", ADDED)
    site = Site()
    site.publish("city", LIVE, release_feed=RELEASE_22, release_version=BASELINE)
    site.serve(aioclient_mock)
    await _setup(hass)
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    city = hass.states.get("sensor.hungarian_transport_city_timetables")
    assert city.attributes["pages_result"] == wt.INSTALLED
    assert "pages_reason" not in city.attributes
    assert served_index.read_feed(card_assets / timetables.CITY_INDEX) == LIVE


@pytest.mark.parametrize("bad", [7, "", "1.10", "latest", "v1.10.24; rm", "v" + "1" * 7 + ".0.0"])
def test_a_bad_release_version_is_refused(bad) -> None:
    site = Site()
    site.publish("city", LIVE, release_version=bad)
    with pytest.raises(mav_daily.DailyIndexError, match="release_version"):
        wt.check_entry("city", site.manifest["indexes"]["city"])


def test_release_version_is_read() -> None:
    site = Site()
    site.publish("city", LIVE, release_version="v1.10.24")
    assert wt.check_entry("city", site.manifest["indexes"]["city"])["release_version"] == "v1.10.24"
    site.publish("city", LIVE)
    assert wt.check_entry("city", site.manifest["indexes"]["city"])["release_version"] is None


def test_a_bad_release_feed_is_refused() -> None:
    site = Site()
    site.publish("city", LIVE, release_feed=7)
    with pytest.raises(mav_daily.DailyIndexError, match="release_feed"):
        wt.check_entry("city", site.manifest["indexes"]["city"])
    site.publish("city", LIVE, release_feed=None)
    assert wt.check_entry("city", site.manifest["indexes"]["city"])["release_feed"] is None


# The signature.


def test_the_shipped_public_key_is_a_raw_ed25519_key() -> None:
    raw = base64.b64decode(_SHIPPED_KEY, validate=True)
    assert len(raw) == 32
    Ed25519PublicKey.from_public_bytes(raw)
    assert _SHIPPED_KEY == "iUfHMDrTTf+3dV7JUu8ktyihkUfpkeTQRQxym955kq8="  # gitleaks:allow (the public key)


def test_the_site_signs_with_the_key_this_version_checks() -> None:
    """scripts/sign_manifest.py signs the deployed manifest; it refuses a
    key that is not this public key's pair, so the two must be one key."""
    signer = Path(__file__).resolve().parents[1] / "scripts" / "sign_manifest.py"
    found = re.findall(r'^PAGES_SIGNING_PUBLIC_KEY = "([^"]+)"$', signer.read_text(encoding="utf-8"), re.M)
    assert found == [_SHIPPED_KEY]


def test_verify_manifest_takes_only_the_key_s_signature_of_these_bytes() -> None:
    body = Site().body()
    wt.verify_manifest(body, _sign(body).encode())
    for sig, message in (
        (_sign(body + b" "), "does not match"),
        (_sign(body, Ed25519PrivateKey.generate()), "does not match"),
        ("%%%", "not base64"),
        (_sign(body).strip() + "\n" + _sign(body), "not base64"),
        (base64.b64encode(b"x" * 10).decode(), "10 bytes"),
        ("", "0 bytes"),
    ):
        with pytest.raises(wt._BadSignature, match=message):
            wt.verify_manifest(body, sig.encode())


@pytest.mark.parametrize(
    "sig",
    [
        {"status": 404},
        "other key",
        "tampered",
        "garbage",
        "A" * (wt.MAX_SIGNATURE + 1),
    ],
)
async def test_a_manifest_without_a_good_signature_installs_nothing(
    hass: HomeAssistant, aioclient_mock, card_assets, caplog, sig
) -> None:
    _install_local(card_assets, "volan", VOLAN_OLD)
    before = _snapshot(card_assets)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    body = site.body()
    well_formed = sig in ("other key", "tampered")
    if sig == "other key":
        sig = _sign(body, Ed25519PrivateKey.generate())
    elif sig == "tampered":
        sig = _sign(body.replace(b"20261102", b"20261103"))
    elif sig == "garbage":
        sig = "not a signature"
    site.serve(aioclient_mock, sig)
    caplog.set_level(logging.INFO)
    # Right after a deploy the cache may pair the manifest with the previous
    # signature: once quiet, then FAILED; a well-formed signature no key
    # verifies is UNSUPPORTED (a key newer than this version: update it).
    pt = wt.PagesTimetables(hass, card_assets)
    results = []
    for _ in range(3):
        await pt._round()
        results.append((await storage.async_load(hass)).get(storage.PAGES_ROUND, {}).get("volan"))
    later = wt.UNSUPPORTED if well_formed else wt.FAILED
    reason = wt.UNKNOWN_KEY if well_formed else mav_daily.REASON_BAD_SIGNATURE
    # The settling round leaves no settled record, but says why it settles.
    assert results[0] == {"settling": reason, "settling_at": results[0]["settling_at"]}
    assert [(r["result"], r["reason"]) for r in results[1:]] == [(later, reason)] * 2
    assert _snapshot(card_assets) == before
    assert set(_requested(aioclient_mock)) == {wt.MANIFEST_URL, wt.SIGNATURE_URL}
    said = [r for r in caplog.records if r.name == wt.__name__ and r.levelno >= logging.INFO]
    assert len(said) == 1, [r.getMessage() for r in said]
    assert said[0].levelno == (logging.INFO if well_formed else logging.WARNING)
    # An unknown key is no failure: no retry waits for it.
    assert (pt._retries == 0) == well_formed
    pt.stop()


async def test_a_signature_that_cannot_be_fetched_fails_at_once(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock, {"status": 500})
    assert await _round(hass, card_assets) == {"volan": wt.FAILED, "city": wt.FAILED}
    assert not (card_assets / timetables.VOLAN_INDEX).exists()


async def test_the_previous_signature_settles_then_the_deploy_s_installs(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    old, new = Site(), Site()
    old.publish("volan", VOLAN_OLD)
    raw, _, _ = new.publish("volan", VOLAN_NEW)
    new.serve(aioclient_mock, _sign(old.body()))
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    aioclient_mock.clear_requests()
    new.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == raw


# A built_at that is no time, and crashes.


async def test_an_impossible_built_at_is_no_time_and_the_round_goes_through(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    raw, _, _ = site.publish("volan", VOLAN_NEW, built_at="2026-02-30T10:00:00Z")
    site.manifest["built_at"] = "2026-02-30T10:00:00Z"
    site.serve(aioclient_mock)
    done = await wt.async_update(hass, card_assets)
    assert done.results["volan"].result == wt.INSTALLED and done.built_at is None
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == raw
    assert mav_daily.built_at({"built_at": "2026-02-30T10:00:00Z"}) is None


async def test_a_crashed_round_is_logged_with_its_traceback_and_retried(
    hass: HomeAssistant, card_assets, monkeypatch, caplog
) -> None:
    calls = []

    async def update(hass_, www):
        calls.append(True)
        if len(calls) in (1, 2, 4):
            raise KeyError("boom")
        return wt.TimetablesRound({"volan": wt.IndexResult(wt.UP_TO_DATE, VOLAN_NEW)})

    monkeypatch.setattr(wt, "async_update", update)
    pt = wt.PagesTimetables(hass, card_assets)
    await pt._round()
    assert pt._retries == 1 and pt._unsub is not None
    assert (await storage.async_load(hass))[storage.PAGES_ROUND]["volan"]["result"] == wt.FAILED
    for _ in range(3):
        await pt._round()
    pt.stop()
    crashes = [r for r in caplog.records if r.name == wt.__name__ and r.levelno == logging.WARNING]
    # The first crash, and the one after a round that went through.
    assert len(crashes) == 2 and all(r.exc_info for r in crashes)


@pytest.mark.parametrize(("error", "traceback"), [(KeyError("boom"), True), (OSError("disk"), False)])
async def test_one_index_crashing_leaves_the_other_and_a_bug_has_its_traceback(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch, caplog, error, traceback
) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.publish("city", CITY_NEW)
    site.serve(aioclient_mock)
    real = wt._update_index

    async def crash(hass_, session, name, *args):
        if name == "volan":
            raise error
        return await real(hass_, session, name, *args)

    monkeypatch.setattr(wt, "_update_index", crash)
    assert await _round(hass, card_assets) == {"volan": wt.FAILED, "city": wt.INSTALLED}
    assert await _round(hass, card_assets) == {"volan": wt.FAILED, "city": wt.UP_TO_DATE}
    said = [r for r in caplog.records if r.name == wt.__name__ and r.levelno == logging.WARNING]
    assert len(said) == 1 and bool(said[0].exc_info) is traceback


async def test_a_failing_notification_still_records_the_round(hass: HomeAssistant, card_assets, monkeypatch, caplog) -> None:
    async def update(hass_, www):
        return wt.TimetablesRound({"volan": wt.IndexResult(wt.UP_TO_DATE, VOLAN_NEW)})

    async def notify(done):
        raise RuntimeError("notify")

    monkeypatch.setattr(wt, "async_update", update)
    pt = wt.PagesTimetables(hass, card_assets, notify)
    await pt._round()
    pt.stop()
    assert (await storage.async_load(hass))[storage.PAGES_ROUND]["volan"]["result"] == wt.UP_TO_DATE
    assert "Timetable update notification failed" in caplog.text


# The commit.


async def test_a_failed_swap_of_a_broken_folder_puts_it_back(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch, caplog
) -> None:
    _, folder = _install_local(card_assets, "volan", VOLAN_OLD, tag="same")
    (card_assets / folder / "01.json.gz").write_bytes(b"broken")
    before = _snapshot(card_assets)
    site = Site()
    site.publish("volan", VOLAN_NEW, tag="same")
    site.serve(aioclient_mock)
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(Path(dst).name)
        if len(calls) == 2:
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(wt, "_replace", flaky)
    assert (await _round(hass, card_assets))["volan"] == wt.FAILED
    # The served index still has the folder it names (broken as it was).
    assert calls[1:] == [folder, folder]
    assert _snapshot(card_assets) == before
    assert not _staged(card_assets)
    assert [w for w in _warnings(caplog) if f"shapes folder {folder}" in w and "No space left" in w]


async def test_a_full_disk_fails_keeps_the_index_and_is_retried(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    raw, _ = _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)

    def full(data, out, suffix=None):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(wt.mav_index, "write_bytes", full)
    pt = wt.PagesTimetables(hass, card_assets)
    await pt._round()
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == raw
    assert not _staged(card_assets)
    assert pt._retries == 1 and pt._unsub is not None
    state = await storage.async_load(hass)
    assert state[storage.PAGES_ROUND]["volan"]["result"] == wt.FAILED
    assert storage.TIMETABLES_INSTALL not in state
    pt.stop()


async def test_a_stop_during_the_commit_waits_for_it_and_reports_nothing(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    import threading

    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    started, release = threading.Event(), threading.Event()
    events: list[str] = []
    real_commit, real_rmtree = wt._commit, wt._rmtree

    def slow_commit(*args):
        started.set()
        release.wait(10)
        real_commit(*args)
        events.append("commit")

    def rmtree(path, *args, **kw):
        if Path(path).name.startswith(wt.STAGING):
            events.append("rmtree")
        return real_rmtree(path, *args, **kw)

    monkeypatch.setattr(wt, "_commit", slow_commit)
    monkeypatch.setattr(wt, "_rmtree", rmtree)
    seen = []

    async def on_result(done):
        seen.append(done)

    pt = wt.PagesTimetables(hass, card_assets, on_result)
    pt.start_round()
    await hass.async_add_executor_job(started.wait, 10)
    task = pt._task
    pt.stop()
    for _ in range(5):
        await asyncio.sleep(0)
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert task.cancelled()
    # The cleanup ran after the commit, never beside it.
    assert events[0] == "commit"
    assert not _staged(card_assets)
    assert seen == []
    state = await storage.async_load(hass)
    assert storage.PAGES_ROUND not in state
    # The committed index is recorded as this module's install (review
    # round 7): without it, it would read as a release's for ever.
    assert state[storage.TIMETABLES_INSTALL]["volan"]["feed"] == VOLAN_NEW
    assert served_index.read_feed(card_assets / timetables.VOLAN_INDEX) == VOLAN_NEW
    assert pt._unsub is None


async def test_a_second_stop_during_the_commit_still_waits_for_it(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    """A cancel during the wait for the shielded commit (a second stop, Home
    Assistant cancelling at shutdown) never lets the cleanup run beside the
    commit, nor loses its record."""
    import threading

    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    started, release = threading.Event(), threading.Event()
    events: list[str] = []
    real_commit, real_rmtree = wt._commit, wt._rmtree

    def slow_commit(*args):
        started.set()
        release.wait(10)
        real_commit(*args)
        events.append("commit")

    def rmtree(path, *args, **kw):
        if Path(path).name.startswith(wt.STAGING):
            events.append("rmtree")
        return real_rmtree(path, *args, **kw)

    monkeypatch.setattr(wt, "_commit", slow_commit)
    monkeypatch.setattr(wt, "_rmtree", rmtree)
    pt = wt.PagesTimetables(hass, card_assets)
    pt.start_round()
    await hass.async_add_executor_job(started.wait, 10)
    task = pt._task
    pt.stop()
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    for _ in range(5):
        await asyncio.sleep(0)
    assert not task.done() and events == []
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert task.cancelled()
    assert events[0] == "commit"
    assert not _staged(card_assets)
    assert (await storage.async_load(hass))[storage.TIMETABLES_INSTALL]["volan"]["feed"] == VOLAN_NEW


async def test_home_assistant_stopping_during_the_commit_waits_for_it(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    """Home Assistant's own stop (hass.async_stop) during the commit (review
    round 8): it cancels the round (a background task) and every executor
    job a background task started, waits only for the tasks not being
    cancelled, and cancels the rest at its last stage. The stop waits for
    the commit; the cleanup never runs beside it; the record is kept."""
    import threading

    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    started, release = threading.Event(), threading.Event()
    events: list[str] = []
    real_commit, real_rmtree = wt._commit, wt._rmtree

    def slow_commit(*args):
        started.set()
        release.wait(20)
        events.append("commit starts")
        real_commit(*args)
        events.append("commit")

    def rmtree(path, *args, **kw):
        if Path(path).name.startswith(wt.STAGING):
            events.append("rmtree")
        return real_rmtree(path, *args, **kw)

    monkeypatch.setattr(wt, "_commit", slow_commit)
    monkeypatch.setattr(wt, "_rmtree", rmtree)
    seen = []

    async def on_result(done):
        seen.append(done)

    pt = wt.PagesTimetables(hass, card_assets, on_result)
    pt.start_round()
    await hass.async_add_executor_job(started.wait, 10)
    task = pt._task
    stopping = asyncio.ensure_future(hass.async_stop())
    for _ in range(50):
        await asyncio.sleep(0.01)
    # Stopping, and waiting for the round: the commit is still blocked.
    assert not stopping.done() and not task.done() and events == []
    release.set()
    await asyncio.wait_for(stopping, 30)
    assert task.cancelled()
    assert events[:2] == ["commit starts", "commit"]
    assert not _staged(card_assets)
    assert served_index.read_feed(card_assets / timetables.VOLAN_INDEX) == VOLAN_NEW
    state = await storage.async_load(hass)
    assert state[storage.TIMETABLES_INSTALL]["volan"]["feed"] == VOLAN_NEW
    assert storage.PAGES_ROUND not in state
    assert seen == []


async def test_the_local_updater_writing_during_a_download_wins(
    hass: HomeAssistant, aioclient_mock, card_assets, mav_source, monkeypatch
) -> None:
    base = mav_source.parent
    base.mkdir(parents=True)
    _install_local(card_assets, "volan", VOLAN_OLD)
    site = Site()
    site.publish("volan", VOLAN_NEW)
    site.serve(aioclient_mock)
    real = wt._download_shard
    wrote: list[bytes] = []

    async def updater_runs(*args):
        if not wrote:
            raw, _ = _install_local(card_assets, "volan", "volan:01716.20261109")
            (base / timetables.VOLAN_INDEX).write_bytes(raw)
            wrote.append(raw)
        await real(*args)

    monkeypatch.setattr(wt, "_download_shard", updater_runs)
    assert (await _round(hass, card_assets))["volan"] == wt.LOCAL_WINS
    assert (card_assets / timetables.VOLAN_INDEX).read_bytes() == wrote[0]
    assert not _staged(card_assets)
    assert storage.TIMETABLES_INSTALL not in await storage.async_load(hass)


async def test_an_installed_index_tells_its_build_time(hass: HomeAssistant, aioclient_mock, card_assets) -> None:
    """The Volán and city indexes have no time in their gzip header: the
    sensors' build time is the file's, set to the entry's built_at."""
    site = Site()
    site.publish("city", CITY_NEW)
    site.publish("volan", VOLAN_NEW, built_at="2999-01-01T00:00:00Z")
    site.serve(aioclient_mock)
    assert await _round(hass, card_assets) == {"volan": wt.INSTALLED, "city": wt.INSTALLED}
    built = dt_util.parse_datetime("2026-11-16T10:40:00Z").timestamp()
    assert served_index.built_at(card_assets / timetables.CITY_INDEX) == built
    assert timetables.scan_index(card_assets / timetables.CITY_INDEX)["updated_at"].timestamp() == built
    # A build time in the future is none: the install's.
    assert served_index.built_at(card_assets / timetables.VOLAN_INDEX) <= time.time()


# Settling.


async def test_two_manifests_taking_turns_settle_once_then_fail(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    """Two generations the cache serves in turn (A, B, A, B), neither's index
    matching: one settle, then FAILED (with its retries), not for ever."""
    a, b = Site(), Site()
    raw_a, _, _ = a.publish("volan", VOLAN_NEW, tag="a")
    b.publish("volan", "volan:01716.20261109", tag="b")
    bodies = [a.body(), b.body()]
    turn = {"n": 0}

    async def manifest(method, url, data):
        turn["n"] += 1
        return AiohttpClientMockResponse(method, url, response=bodies[turn["n"] % 2])

    async def signature(method, url, data):
        return AiohttpClientMockResponse(method, url, text=_sign(bodies[turn["n"] % 2]))

    aioclient_mock.get(wt.MANIFEST_URL, side_effect=manifest)
    aioclient_mock.get(wt.SIGNATURE_URL, side_effect=signature)
    aioclient_mock.get(f"{mav_daily.PAGES_BASE}/volan-index.json.gz", content=_flipped(raw_a))
    results = [(await _round(hass, card_assets))["volan"] for _ in range(6)]
    assert results == [wt.SETTLING] + [wt.FAILED] * 5
    # A first or daily round may settle again.
    wt.new_cycle(hass)
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING


async def test_an_index_the_site_no_longer_has_settles_then_fails(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    body = site.files.pop("volan-index.json.gz")
    site.serve(aioclient_mock)
    aioclient_mock.get(f"{mav_daily.PAGES_BASE}/volan-index.json.gz", status=404, content=body)
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    assert (await _round(hass, card_assets))["volan"] == wt.FAILED


async def test_a_retry_downloads_only_the_shards_still_missing(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "CONCURRENCY", 1)
    site = Site()
    _, folder, shards = site.publish("volan", VOLAN_NEW)
    good = site.files[f"{folder}/03.json.gz"]
    site.files[f"{folder}/03.json.gz"] = b"x" * len(good)
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.SETTLING
    (staging,) = _staged(card_assets)
    assert sorted(p.name for p in staging.iterdir()) == ["00.json.gz", "01.json.gz", "02.json.gz"]
    aioclient_mock.clear_requests()
    site.files[f"{folder}/03.json.gz"] = good
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert [u for u in _requested(aioclient_mock) if "-shapes-" in u] == [f"{mav_daily.PAGES_BASE}/{folder}/03.json.gz"]
    assert {p.name: p.read_bytes() for p in (card_assets / folder).iterdir()} == shards
    assert not _staged(card_assets)


# Feeds dated far ahead.


def test_a_feed_dated_far_ahead_is_refused_and_never_freezes_the_index(tmp_path) -> None:
    site = Site()
    site.publish("volan", "volan:01716.29991231")
    site.publish("city", "bkk:20271231;miskolc:1.3")
    for name in ("volan", "city"):
        with pytest.raises(mav_daily.DailyIndexError, match="days ahead"):
            wt.check_entry(name, site.manifest["indexes"][name], TODAY)
    # Within MAX_FEED_AHEAD: a date.
    site.publish("city", "bkk:20271201;miskolc:1.3")
    assert wt.check_entry("city", site.manifest["indexes"]["city"], TODAY)
    # Served, such a date does not hold back every later build.
    installed = "bkk:20271231;miskolc:1.3;szeged:20261020"
    assert wt.city_newer("bkk:20261124;miskolc:1.4;szeged:20261124", installed, TODAY)
    assert not wt.city_newer("bkk:20261124;miskolc:1.3;szeged:20261013", installed, TODAY)
    www = tmp_path / "served"
    www.mkdir()
    (www / timetables.VOLAN_INDEX).write_bytes(_index("volan", "volan:01716.29991231", None))
    assert wt.decide("volan", "volan:01716.20261124", www, tmp_path, today=TODAY) == wt._INSTALL


# The rounds.


async def test_a_daily_round_replaces_a_waiting_retry_and_never_runs_beside_a_slow_one(
    hass: HomeAssistant, card_assets, monkeypatch
) -> None:
    calls = []
    gate = asyncio.Event()

    async def update(hass_, www):
        calls.append(True)
        if len(calls) == 1:
            return wt.TimetablesRound({"volan": wt.IndexResult(wt.FAILED, error="x")})
        await gate.wait()
        return wt.TimetablesRound({"volan": wt.IndexResult(wt.UP_TO_DATE, VOLAN_NEW)})

    monkeypatch.setattr(wt, "async_update", update)
    pt = wt.PagesTimetables(hass, card_assets)
    pt.start_round()
    await hass.async_block_till_done(wait_background_tasks=True)
    retry = pt._unsub
    assert retry is not None and pt._retries == 1
    # The daily round comes while the retry waits: it is the round now.
    pt.start_round()
    assert pt._retries == 0
    await _until(lambda: len(calls) == 2)
    task = pt._task
    # ... and another while that one still downloads: no second task.
    pt.start_round()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=wt.RETRY_DELAY + 1))
    for _ in range(10):
        await asyncio.sleep(0)
    assert len(calls) == 2 and pt._task is task
    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 2 and pt._unsub is None
    pt.stop()


def test_check_index_refuses_what_does_not_unpack_or_unpacks_to_too_much(monkeypatch) -> None:
    site = Site()
    site.publish("volan", VOLAN_NEW)
    entry = wt.check_entry("volan", site.manifest["indexes"]["volan"])

    def entry_for(body: bytes) -> dict:
        return {**entry, "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    broken = gzip.compress(b"x")[:10] + b"\xff" * 64
    with pytest.raises(mav_daily.DailyIndexError, match="does not unpack"):
        wt.check_index(broken, entry_for(broken))
    monkeypatch.setattr(wt, "MAX_INDEX_JSON", 1000)
    bomb = gzip.compress(b'{"v":29,' + b" " * 10_000 + b"}")
    with pytest.raises(mav_daily.DailyIndexError, match="unpacks to more than 1000"):
        wt.check_index(bomb, entry_for(bomb))


# --- review round 7: the gaps ----------------------------------------------------


@pytest.mark.parametrize("failure", [{"status": 500}, {"exc": asyncio.TimeoutError()}], ids=["http-500", "timeout"])
async def test_a_failed_shard_keeps_the_verified_ones_for_the_retry(
    hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch, failure
) -> None:
    """Not a mismatch (no settling): a shard that failed to download. The
    round is FAILED, and the shards that checked out stay for the retry."""
    monkeypatch.setattr(wt, "CONCURRENCY", 1)
    site = Site()
    _, folder, shards = site.publish("volan", VOLAN_NEW)
    good = site.files.pop(f"{folder}/03.json.gz")
    site.serve(aioclient_mock)
    aioclient_mock.get(f"{mav_daily.PAGES_BASE}/{folder}/03.json.gz", **failure)
    done = await _real_update(hass, card_assets)
    assert done.results["volan"].result == wt.FAILED
    (staging,) = _staged(card_assets)
    assert sorted(p.name for p in staging.iterdir()) == ["00.json.gz", "01.json.gz", "02.json.gz"]
    aioclient_mock.clear_requests()
    site.files[f"{folder}/03.json.gz"] = good
    site.serve(aioclient_mock)
    assert (await _round(hass, card_assets))["volan"] == wt.INSTALLED
    assert [u for u in _requested(aioclient_mock) if "-shapes-" in u] == [f"{mav_daily.PAGES_BASE}/{folder}/03.json.gz"]
    assert {p.name: p.read_bytes() for p in (card_assets / folder).iterdir()} == shards
    assert not _staged(card_assets)


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_staging_replaces_a_symlink_or_a_file_of_its_name(tmp_path, kind) -> None:
    """The staging folder's name taken by a symlink (to a folder elsewhere,
    which must not get the shards) or a file: replaced by a folder."""
    site = Site()
    site.publish("volan", VOLAN_NEW)
    entry = wt.check_entry("volan", site.manifest["indexes"]["volan"])
    key = wt.staging_key(entry)
    path = tmp_path / f"{wt.STAGING}{entry['shapes']['dir']}-{key}"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if kind == "symlink":
        path.symlink_to(elsewhere, target_is_directory=True)
    else:
        path.write_bytes(b"x")
    tmp, missing = wt._staging(tmp_path, entry["shapes"], key)
    assert tmp == path and tmp.is_dir() and not tmp.is_symlink()
    assert tmp.stat().st_mode & 0o777 == 0o755
    assert len(missing) == SHARDS
    assert elsewhere.is_dir() and not list(elsewhere.iterdir())


def test_staging_touches_a_folder_a_failed_try_left(tmp_path) -> None:
    """Its shards stay, and so does the folder: touched, prune takes it for
    no leftover (TEMP_MIN_AGE) while the retry uses it."""
    site = Site()
    _, folder, shards = site.publish("volan", VOLAN_NEW)
    entry = wt.check_entry("volan", site.manifest["indexes"]["volan"])
    key = wt.staging_key(entry)
    path = tmp_path / f"{wt.STAGING}{folder}-{key}"
    path.mkdir()
    (path / "00.json.gz").write_bytes(shards["00.json.gz"])
    (path / "01.json.gz").write_bytes(b"broken")
    old = time.time() - 2 * wt.TEMP_MIN_AGE
    os.utime(path, (old, old))
    tmp, missing = wt._staging(tmp_path, entry["shapes"], key)
    assert tmp == path
    assert missing == ["01.json.gz", "02.json.gz", "03.json.gz"]
    assert time.time() - tmp.stat().st_mtime < 60
    (tmp_path / timetables.VOLAN_INDEX).write_bytes(_index("volan", VOLAN_OLD, None))
    wt.prune(tmp_path, "volan", None)
    assert path.is_dir()


async def test_a_shard_shorter_than_its_manifest_is_a_mismatch(
    hass: HomeAssistant, aioclient_mock, card_assets
) -> None:
    site = Site()
    _, folder, _ = site.publish("volan", VOLAN_NEW)
    good = site.files[f"{folder}/02.json.gz"]
    site.files[f"{folder}/02.json.gz"] = good[:-3]
    site.serve(aioclient_mock)
    done = await _real_update(hass, card_assets)
    res = done.results["volan"]
    assert res.result == wt.SETTLING
    assert res.reason == mav_daily.REASON_MISMATCH
    assert f"02.json.gz is {len(good) - 3} bytes, the manifest says {len(good)}" in res.error
    for staging in _staged(card_assets):
        assert "02.json.gz" not in {p.name for p in staging.iterdir()}


async def test_a_settling_round_keeps_the_settled_record_and_says_why(hass: HomeAssistant) -> None:
    await storage.async_record_pages_round(hass, {"volan": (wt.FAILED, VOLAN_NEW, "not_found")})
    first = dict((await storage.async_load(hass))[storage.PAGES_ROUND]["volan"])
    await storage.async_record_pages_round(hass, {}, {"volan": mav_daily.REASON_MISMATCH})
    settling = (await storage.async_load(hass))[storage.PAGES_ROUND]["volan"]
    assert {k: settling[k] for k in first} == first
    assert settling["settling"] == mav_daily.REASON_MISMATCH
    await storage.async_record_pages_round(hass, {"volan": (wt.INSTALLED, VOLAN_NEW, None)})
    settled = (await storage.async_load(hass))[storage.PAGES_ROUND]["volan"]
    assert settled["result"] == wt.INSTALLED and "settling" not in settled and "reason" not in settled


async def test_the_sensors_say_why_an_index_was_not_installed(
    hass: HomeAssistant, aioclient_mock, card_assets, freezer, morning, monkeypatch
) -> None:
    monkeypatch.setattr(wt, "async_update", _real_update)
    _install_local(card_assets, "volan", VOLAN_NEW)
    _install_local(card_assets, "city", CITY_OLD)
    site = Site()
    _, folder, _ = site.publish("city", CITY_NEW)
    body = site.files.pop(f"{folder}/01.json.gz")
    site.publish("volan", VOLAN_OLD)
    site.serve(aioclient_mock)
    aioclient_mock.get(f"{mav_daily.PAGES_BASE}/{folder}/01.json.gz", status=404, content=body)
    await _setup(hass)
    await _at(hass, freezer, "2026-11-16 03:20:01+00:00")
    city = hass.states.get("sensor.hungarian_transport_city_timetables")
    volan = hass.states.get("sensor.hungarian_transport_volan_timetable")
    # The 404 settles first: no settled record yet, but why it settles.
    assert city.attributes["pages_settling"] == mav_daily.REASON_NOT_FOUND
    assert "pages_result" not in city.attributes or city.attributes["pages_result"] is None
    assert volan.attributes["pages_result"] == wt.SUPERSEDED
    assert volan.attributes["pages_reason"] == wt.SERVED_NEWER
    await _at(hass, freezer, "2026-11-16 03:31:02+00:00")
    city = hass.states.get("sensor.hungarian_transport_city_timetables")
    assert city.attributes["pages_result"] == wt.FAILED
    assert city.attributes["pages_reason"] == mav_daily.REASON_NOT_FOUND
    assert "pages_settling" not in city.attributes


# The site's signer (scripts/sign_manifest.py, openssl) and the integration's
# check (cryptography) are one format: a signature one writes, the other takes.
_SIGNER_WRAP = (
    "import os, sys; sys.path.insert(0, sys.argv.pop(1)); import sign_manifest;"
    " sign_manifest.PUBLIC_KEY = bytes.fromhex(os.environ['TEST_PAGES_PUBLIC_KEY']);"
    " raise SystemExit(sign_manifest.main(sys.argv[1:]))"
)


def _openssl_signs(tmp_path: Path) -> bool:
    """Whether openssl here signs raw ed25519 (3.0 or later, pkeyutl -rawin)."""
    import shutil
    import subprocess

    if shutil.which("openssl") is None:
        return False
    data = tmp_path / "probe"
    data.write_bytes(b"probe")
    seed = os.urandom(32)
    done = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-keyform", "DER", "-inkey", "/dev/stdin", "-in", str(data)],
        input=bytes.fromhex("302e020100300506032b657004220420") + seed, capture_output=True, timeout=60, check=False,
    )
    return done.returncode == 0 and len(done.stdout) == 64


def _seed_and_public() -> tuple[bytes, str]:
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = base64.b64encode(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
    return seed, public


def test_a_manifest_signed_by_the_site_s_signer_verifies_here(tmp_path) -> None:
    import subprocess
    import sys

    if not _openssl_signs(tmp_path):
        pytest.skip("no openssl 3 here (pkeyutl -rawin)")
    seed, public = _seed_and_public()
    site = tmp_path / "site"
    site.mkdir()
    s = Site()
    s.publish("volan", VOLAN_NEW)
    (site / "timetables.json").write_bytes(s.body())
    env = {**os.environ, "PAGES_SIGNING_KEY": base64.b64encode(seed).decode(),
           "TEST_PAGES_PUBLIC_KEY": base64.b64decode(public).hex()}
    done = subprocess.run([sys.executable, "-c", _SIGNER_WRAP, str(ROOT / "scripts"), "sign", "--site", str(site)],
                          capture_output=True, text=True, timeout=120, env=env, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    # The seed never reaches the output.
    assert base64.b64encode(seed).decode() not in done.stdout + done.stderr
    raw, sig = (site / "timetables.json").read_bytes(), (site / "timetables.json.sig").read_bytes()
    wt.verify_manifest(raw, sig, public)
    wt.verify_manifest(raw, sig, (_PUBLIC, public))
    with pytest.raises(mav_daily.UnknownKey):
        wt.verify_manifest(raw, sig, _PUBLIC)
    with pytest.raises(mav_daily.UnknownKey):
        wt.verify_manifest(raw + b" ", sig, public)


def test_a_mav_manifest_signed_by_the_site_s_signer_verifies_here(tmp_path) -> None:
    """mav-index.json.sig: the same format, the same key (the signer's
    sign over any file's bytes)."""
    import importlib.util

    if not _openssl_signs(tmp_path):
        pytest.skip("no openssl 3 here (pkeyutl -rawin)")
    spec = importlib.util.spec_from_file_location("_sign_manifest_under_test", ROOT / "scripts" / "sign_manifest.py")
    sm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sm)
    seed, public = _seed_and_public()
    assert sm.public_key(seed) == base64.b64decode(public)
    manifest = tmp_path / "mav-index.json"
    manifest.write_bytes(json.dumps({"format": 1, "feed": "20261101.M2ChangeID"}).encode())
    sig = (base64.b64encode(sm.sign(manifest, seed)).decode() + "\n").encode()
    mav_daily.verify_signature(manifest.read_bytes(), sig, "mav-index.json", public)
    # And the other way: a signature made here verifies by the signer's check.
    assert sm.verify(manifest, sm.decode_sig(sig), base64.b64decode(public))
