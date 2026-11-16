"""The weekly Volán and city indexes from GitHub Pages (weekly_timetables.py)."""

import gzip
import hashlib
import json
import logging
import os
import re
from pathlib import Path

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.hungarian_transport import (
    mav_daily,
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


@pytest.fixture
def no_pages_timetables():
    """Overrides the conftest fixture: these tests run the real rounds."""
    yield


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

    def serve(self, aioclient_mock) -> None:
        aioclient_mock.get(wt.MANIFEST_URL, json=self.manifest)
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
    return {str(p.relative_to(www)): p.read_bytes() for p in sorted(www.rglob("*")) if p.is_file()}


def _requested(aioclient_mock) -> list[str]:
    return [str(call[1]) for call in aioclient_mock.mock_calls]


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == wt.__name__ and r.levelno >= logging.WARNING]


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
    assert _requested(aioclient_mock) == [wt.MANIFEST_URL, wt.MANIFEST_URL]
    assert _snapshot(card_assets) == before
    # Said once, as information: an update of the integration reads it.
    assert caplog.text.count("Weekly timetables from GitHub Pages skipped") == 1
    assert _warnings(caplog) == []


async def test_a_broken_manifest_fails_and_is_logged_once(hass: HomeAssistant, aioclient_mock, card_assets, caplog) -> None:
    aioclient_mock.get(wt.MANIFEST_URL, text="{not json")
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
    assert not [p for p in card_assets.iterdir() if ".tmp" in p.name or ".json.gz." in p.name]
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
    assert _requested(aioclient_mock) == [wt.MANIFEST_URL]
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
        assert _requested(aioclient_mock) == [wt.MANIFEST_URL]


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
    assert not [p for p in card_assets.iterdir() if ".tmp" in p.name]
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
    now = 2_000_000_000.0
    for path in (temp, stray):
        os.utime(path, (now - 10, now - 10))

    wt.prune(www, "volan", keep, now=now)
    assert (old / wt.SHAPES_RETIRED).is_file() and not (www / keep / wt.SHAPES_RETIRED).exists()
    # Another stem, and young temp files (another writer's), are left alone.
    assert not (city / wt.SHAPES_RETIRED).exists() and temp.is_dir() and stray.is_file()

    wt.prune(www, "volan", keep, now=now + wt.SHAPES_GRACE - 1)
    assert old.is_dir()
    wt.prune(www, "volan", keep, now=now + wt.SHAPES_GRACE)
    assert not old.exists() and not temp.exists() and not stray.exists()
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
    real_replace = wt.os.replace
    real_write = wt.mav_index.write_bytes

    def replace(src, dst):
        if Path(src).is_dir():
            order.append(f"folder {Path(dst).name}")
        return real_replace(src, dst)

    def write(data, out, suffix=None):
        assert (card_assets / folder).is_dir()
        order.append(f"index {Path(out).name}")
        return real_write(data, out, suffix)

    monkeypatch.setattr(wt.os, "replace", replace)
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
    assert not [p for p in card_assets.iterdir() if ".old" in p.name or ".tmp" in p.name]


async def test_shards_download_at_most_concurrency_at_a_time(hass: HomeAssistant, aioclient_mock, card_assets, monkeypatch) -> None:
    import asyncio

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
        "New weekly timetables from GitHub Pages are installed: Volán buses, city lines.",
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
