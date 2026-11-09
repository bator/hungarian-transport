"""GTFS-Realtime decoding, matching, caching and the realtime service.

The .pb fixtures are real feeds of 2026-11-02 07:49 UTC (jbb.ghsq.de),
trimmed to a few trips with their vehicle descriptors (plates) and entity
ids removed; mav-static.zip is three rows of that day's MÁV trips.txt plus
one made-up trip with a reused train number.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
import re
import zipfile

import pytest
import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.hungarian_transport as integration
from custom_components.hungarian_transport import realtime
from custom_components.hungarian_transport.const import DOMAIN, SERVICE_REALTIME

FIX = Path(__file__).parent / "fixtures" / "realtime"
NOW = 1793605800  # 2026-11-02 07:50:00 UTC
FROZEN = "2026-11-02T07:50:00+00:00"
J = "https://jbb.ghsq.de/gtfs/hu/"
SZFV = J + "hkir_hkir_V-14827/"
MAV_STATIC = "https://jbb.ghsq.de/gtfs/hu-m%C3%A1v_szem%C3%A9lysz%C3%A1ll%C3%ADt%C3%A1si_zrt..gtfs.zip"
GYSEV_STATIC = "https://jbb.ghsq.de/gtfs/hu-gysev_zrt..gtfs.zip"


def _pb(name: str) -> bytes:
    return (FIX / f"{name}.pb").read_bytes()


def _feed(name: str, prefix: str) -> realtime.Feed:
    return realtime.decode_feed(_pb(name), prefix)


# ---------------------------------------------------------------- decoding


def test_decodes_trip_updates_with_negative_delays() -> None:
    feed = _feed("volan-szekesfehervar-trip-updates", "hkir:")
    assert feed.timestamp == 1793605797
    tu = feed.trip_updates["hkir_H6192_619-1"]
    assert tu.start_date == "20261102"
    first = tu.stops[0]
    # Arrived five minutes early, leaves 8 s late: the ten-byte negative varint.
    assert (first.stop_id, first.seq, first.arr_delay, first.dep_delay) == ("hkir_665935_1", 2, -299, 8)
    assert len(tu.stops) == 12
    assert len(feed.trip_updates) == 3 and not feed.vehicles


def test_decodes_vehicle_positions() -> None:
    feed = _feed("volan-szekesfehervar-vehicle-positions", "hkir:")
    v = feed.vehicles["hkir_H6112_607-1"]
    assert v.route_id == "hkir_V-14827-H-3-12"
    assert v.lat == pytest.approx(47.19302, abs=1e-5) and v.lon == pytest.approx(18.40365, abs=1e-5)
    assert v.bearing == 327.0 and v.timestamp == 1793605748
    assert (v.stop_id, v.status) == ("hkir_665955_2", 1)


def test_fixtures_carry_no_vehicle_plates() -> None:
    for path in FIX.glob("*.pb"):
        assert not re.search(rb"[A-Z]{3}\d{3}", path.read_bytes()), path.name


@pytest.mark.parametrize(
    "data",
    [b"", b"\x0a\x05abc", b"\x12\xff\xff\xff\xff\x0f", b"\x0b", b"<html>not protobuf</html>", b"\x80" * 12],
)
def test_garbage_is_a_decode_error(data: bytes) -> None:
    with pytest.raises(realtime.RtDecodeError):
        realtime.decode_feed(data)


def test_truncated_feed_is_a_decode_error() -> None:
    data = _pb("volan-trip-updates")
    with pytest.raises(realtime.RtDecodeError):
        realtime.decode_feed(data[: len(data) - 7], "hkir:")


def _ld(num: int, body: bytes) -> bytes:
    return bytes([(num << 3) | 2, len(body)]) + body


def test_alerts_and_deleted_entities() -> None:
    header = _ld(1, b"\x0a\x032.0")
    text = _ld(1, _ld(1, b"Detour") + _ld(2, b"en")) + _ld(1, _ld(1, "Terelés".encode()) + _ld(2, b"hu"))
    informed = _ld(5, _ld(2, b"hkir:R1") + _ld(5, b"hkir:S1"))
    alert = _ld(5, informed + _ld(10, text))
    deleted = _ld(2, _ld(1, b"x") + b"\x10\x01" + _ld(4, _ld(1, _ld(1, b"hkir:T")) + _ld(2, b"\x0d\x00\x00\x3c\x42\x15\x00\x00\x98\x41")))
    feed = realtime.decode_feed(header + _ld(2, _ld(1, b"a") + alert) + deleted, "hkir:")
    (a,) = feed.alerts
    assert (a.header, a.routes, a.stops) == ("Terelés", ["R1"], ["S1"])
    assert not feed.vehicles


def test_train_numbers_from_static() -> None:
    trains = realtime.train_numbers_from_static((FIX / "mav-static.zip").read_bytes(), "1:")
    assert trains["822"] == ["35700507"]
    assert trains["4272"] == ["36016774", "99999999"]
    assert trains["9442"] == ["36166415"]


# ---------------------------------------------------------------- feed table


def test_feed_table_covers_the_operators() -> None:
    table = realtime.load_feed_table()
    doc = json.loads(realtime.FEEDS_FILE.read_text(encoding="utf-8"))
    volan_local = [k for k in doc["feeds"] if k.startswith("volan-")]
    assert len(volan_local) == 59
    for fid in ("volan", "paks", "veszprem", "miskolc", "debrecen", "oroshaza", "mav", "gysev", "dkv"):
        assert fid in table, fid
    assert table["dkv"] is table["debrecen"]
    assert table["volan"].trip_updates_interval == 120
    for cfg in table.values():
        assert cfg.interval >= realtime.MIN_INTERVAL
        for src in cfg.sources:
            for url in (src.trip_updates, src.vehicle_positions, src.static):
                assert not url or url.startswith("https://")
    assert table["mav"].match == {"train"}
    assert {s.static for s in table["mav"].sources} == {MAV_STATIC, GYSEV_STATIC}


def test_bad_feed_table_entries_are_skipped(caplog) -> None:
    table = realtime.parse_feed_table(
        {
            "feeds": {
                "ok": {"sources": [{"trip_updates": "https://x/tu.pb", "prefix": "a:"}], "match": ["trip"], "interval": 5},
                "Bad Id": {"sources": [{"trip_updates": "https://x"}], "match": ["trip"]},
                "plain": {"sources": [{"trip_updates": "http://x/tu.pb"}], "match": ["trip"]},
                "nomatch": {"sources": [{"trip_updates": "https://x/tu.pb"}], "match": ["guess"]},
            },
            "aliases": {"alias": "ok", "dangling": "missing"},
        }
    )
    assert set(table) == {"ok", "alias"}
    # Never more often than MIN_INTERVAL, whatever the table says.
    assert table["ok"].interval == realtime.MIN_INTERVAL
    assert caplog.text.count("Skipping realtime feed") == 3


# ---------------------------------------------------------------- matching


def _loaded(name: str, prefix: str, trains: dict | None = None, static: str = ""):
    src = realtime.Source("tu", "vp", prefix, static)
    return [(src, _feed(f"{name}-trip-updates", prefix), _feed(f"{name}-vehicle-positions", prefix), trains)]


def _cfg(*match: str) -> realtime.FeedConfig:
    return realtime.FeedConfig("t", (), frozenset(match), 60, 60)


def _answer(cfg, loaded, **req):
    req.setdefault("key", "k")
    return realtime._answer(cfg, loaded, req, NOW)


def test_trip_id_match_gives_the_delay_at_the_boarding_and_alighting_stops() -> None:
    loaded = _loaded("volan-szekesfehervar", "hkir:")
    # The card sends parent stop ids; the feed has their platforms (_1, _2, _19).
    hit = _answer(
        _cfg("trip"), loaded, trip_id="hkir_H6112_607-1",
        stops=["hkir_666065", "hkir_666007", "hkir_666083", "hkir_666060", "hkir_665955", "hkir_666225"],
        **{"from": 4, "to": 5},
    )
    assert hit["trip_id"] == "hkir_H6112_607-1"
    assert (hit["delay"], hit["dest_delay"], hit["approx"]) == (190, 181, False)
    assert hit["vehicle"]["lat"] == pytest.approx(47.19302, abs=1e-5)
    assert hit["vehicle"]["timestamp"] == 1793605748


def test_a_stop_without_its_own_update_takes_the_earlier_stops_delay() -> None:
    loaded = _loaded("volan-szekesfehervar", "hkir:")
    hit = _answer(
        _cfg("trip"), loaded, trip_id="hkir_H6112_607-1",
        stops=["hkir_666060", "hkir_999999"], **{"from": 1},
    )
    assert (hit["delay"], hit["approx"]) == (119, False)


def test_an_implausible_delay_is_dropped_but_the_position_stays() -> None:
    loaded = _loaded("volan-szekesfehervar", "hkir:")
    # 611-1 reports -5108 s (85 minutes early) everywhere: a vehicle on the
    # wrong trip, not a prediction.
    hit = _answer(_cfg("trip"), loaded, trip_id="hkir_H6112_611-1", stops=["hkir_666065"], **{"from": 0})
    assert hit["delay"] is None and hit["vehicle"] is not None


def test_prefixed_and_unknown_trip_ids() -> None:
    loaded = _loaded("volan", "hkir:")
    assert _answer(_cfg("trip"), loaded, trip_id="hkir:hkir_3624_6-2")["trip_id"] == "hkir_3624_6-2"
    assert _answer(_cfg("trip"), loaded, trip_id="hkir_0000_0-0") is None
    # Another service day is another run.
    assert _answer(_cfg("trip"), loaded, trip_id="hkir_3624_6-2", date="20261103") is None


def test_stale_position_is_not_live() -> None:
    loaded = _loaded("volan", "hkir:")
    hit = realtime._answer(_cfg("trip"), loaded, {"key": "k", "trip_id": "hkir_4194_7-2"}, NOW + 3600)
    assert hit["vehicle"] is None
    # Not the stale vehicle's stop either: the first update's delay.
    assert hit["delay"] == 775 and hit["approx"] is True


def test_route_and_start_match_for_feeds_with_other_trip_ids() -> None:
    loaded = _loaded("veszprem", "VBUSZ:")
    cfg = _cfg("trip", "route_start")
    hit = _answer(cfg, loaded, trip_id="1692150", route="5", start=525, date="20261102", stops=["SP1825"], **{"from": 0})
    assert hit["trip_id"] == "1140_1446_084500_A_HP" and hit["delay"] == 19
    # Two route 3 trips start at 08:43 from both ends: the card's stop picks one.
    hit = _answer(cfg, loaded, route="3", start=523, stops=["SP1724"], **{"from": 0})
    assert hit["trip_id"] == "1140_2058_084300_A_HP" and hit["delay"] == 111
    hit = _answer(cfg, loaded, route="3", start=523, stops=["SP1826"], **{"from": 0})
    assert hit["trip_id"] == "1140_1457_084300_A_HP"
    # Without a stop to tell them apart it is neither.
    assert _answer(cfg, loaded, route="3", start=523) is None
    # A trip past midnight (start 1963 = 08:43 next day) is the same clock time.
    assert _answer(cfg, loaded, route="3", start=523 + 1440, stops=["SP1826"], **{"from": 0}) is not None


def test_feed_with_foreign_stop_ids_gives_the_current_delay() -> None:
    loaded = _loaded("miskolc", "mvkzrt:")
    hit = _answer(_cfg("route_start"), loaded, route="32", start=515, stops=["638481"], **{"from": 0})
    assert hit["trip_id"] == "10976_59_083500_B4O"
    # The update at the vehicle's current stop (SP375).
    assert hit["approx"] is True and hit["delay"] == -21
    assert hit["vehicle"]["lat"] == pytest.approx(48.08612, abs=1e-5)


def test_trip_updates_take_the_route_their_pattern_had_in_a_vehicle() -> None:
    # The OTP city feeds put route_id only in the vehicle's descriptor.
    (src, tu, vp, _tr) = _loaded("miskolc", "mvkzrt:")[0]
    assert all(not t.route_id for t in tu.trip_updates.values())
    # 08:28's vehicle was never seen; 08:19's, of the same pattern, was.
    del vp.vehicles["10842_1943_082800_AZN"]
    known: dict[str, str] = {}
    realtime._learn_routes(known, vp)
    realtime._fill_routes(known, tu)
    loaded = [(src, tu, None, None)]
    hit = _answer(_cfg("route_start"), loaded, route="1V", start=508, stops=["x"], **{"from": 0})
    assert hit["trip_id"] == "10842_1943_082800_AZN" and hit["vehicle"] is None
    assert _answer(_cfg("route_start"), loaded, route="1", start=508) is None
    # A pattern seen on two routes names neither.
    known = {}
    realtime._learn_routes(known, vp)
    vp.vehicles["10842_1943_081900_AZN"].route_id = "2"
    realtime._learn_routes(known, vp)
    assert realtime._route_of(known, "10842_1943_082800_AZN") == ""
    # An id without the OTP shape is its own key.
    assert realtime._route_key("hkir_3624_6-2") == "hkir_3624_6-2"


def test_train_number_match() -> None:
    trains = realtime.train_numbers_from_static((FIX / "mav-static.zip").read_bytes(), "1:")
    loaded = _loaded("mav", "1:", trains)
    cfg = _cfg("train")
    # IC 822 from station 005501024 (its _7 platform), 10 minutes late at 005506189.
    hit = _answer(cfg, loaded, train="822", stops=["005501024", "005506189"], **{"from": 0, "to": 1})
    assert hit["trip_id"] == "35700507"
    assert (hit["delay"], hit["dest_delay"], hit["approx"]) == (0, 600, False)
    # The other 4272 (99999999) is not in the feed; leading zeros are no other train.
    hit = _answer(cfg, loaded, train="04272", stops=["005505868"], **{"from": 0})
    assert hit["trip_id"] == "36016774" and hit["delay"] == 60
    # A station the update list skips: the train's current delay.
    hit = _answer(cfg, loaded, train="4272", stops=["005599999"], **{"from": 0})
    assert hit["approx"] is True and hit["delay"] == 60
    assert _answer(cfg, loaded, train="1") is None


def test_canceled_trip() -> None:
    header = _ld(1, b"\x0a\x032.0")
    tu = _ld(3, _ld(1, _ld(1, b"T1") + b"\x20\x03") + _ld(2, b"\x08\x01" + _ld(3, b"\x08\x3c") + _ld(4, b"S1")))
    feed = realtime.decode_feed(header + _ld(2, _ld(1, b"e") + tu))
    loaded = [(realtime.Source("tu", "", "", ""), feed, None, None)]
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["S1"], **{"from": 0})
    assert hit["canceled"] is True and hit["delay"] is None


def _stu(stop: bytes, seq: int, delay: int | None = None, rel: int = 0, when: int | None = None) -> bytes:
    """A StopTimeUpdate with its departure event (delay and time not negative)."""
    event = (b"\x08" + _varint(delay) if delay is not None else b"") + (
        b"\x10" + _varint(when) if when is not None else b""
    )
    return _ld(2, b"\x08" + bytes([seq]) + (_ld(3, event) if event else b"") + _ld(4, stop) + (
        b"\x28" + bytes([rel]) if rel else b""
    ))


def _varint(value: int) -> bytes:
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes([byte | 0x80])
        else:
            return out + bytes([byte])


def _one_trip(*updates: bytes, trip_rel: int = 0, timestamp: int = NOW) -> list:
    header = _ld(1, b"\x0a\x032.0" + b"\x18" + _varint(timestamp))
    desc = _ld(1, _ld(1, b"T1") + (b"\x20" + bytes([trip_rel]) if trip_rel else b""))
    feed = realtime.decode_feed(header + _ld(2, _ld(1, b"e") + _ld(3, desc + b"".join(updates))))
    return [(realtime.Source("tu", "", "", ""), feed, None, None)]


def test_a_skipped_boarding_stop_has_no_delay_and_says_so() -> None:
    # The bus is 10 minutes late at A but passes B by (a detour): B gets
    # neither A's delay nor the current-delay estimate.
    loaded = _one_trip(_stu(b"A", 1, 120), _stu(b"B", 2, rel=1), _stu(b"C", 3, 90))
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B", "C"], **{"from": 1, "to": 2})
    assert (hit["delay"], hit["approx"], hit["skipped"]) == (None, False, True)
    assert hit["dest_delay"] == 90
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B"], **{"from": 0, "to": 1})
    assert (hit["delay"], hit["dest_delay"], hit["skipped"]) == (120, None, True)
    # A skipped stop before a boarding stop the feed does not know is passed
    # over: the current delay, as before.
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["B", "X"], **{"from": 1})
    assert (hit["delay"], hit["approx"], hit["skipped"]) == (120, True, False)


def test_no_data_at_the_boarding_stop_is_no_estimate_either() -> None:
    loaded = _one_trip(_stu(b"A", 1, 120), _stu(b"B", 2, rel=2))
    for stops, frm in ((["A", "B"], 1), (["A", "B", "X"], 2)):
        hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=stops, **{"from": frm})
        assert hit is None or (hit["delay"], hit["approx"]) == (None, False)


def test_a_loop_trip_takes_each_stops_own_call() -> None:
    # A ... B ... A: the second A is the alighting stop, 7 minutes late.
    loaded = _one_trip(_stu(b"A", 1, 0, when=1000), _stu(b"B", 2, 60), _stu(b"A", 3, 120, when=2000))
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B", "A"], repeat=[0, 2], **{"from": 1, "to": 2})
    assert (hit["delay"], hit["dest_delay"], hit["dest_time"]) == (60, 120, 2000)
    # Boarding at the loop's last call.
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B", "A"], repeat=[0, 2], **{"from": 2})
    assert (hit["delay"], hit["time"]) == (120, 2000)
    # A feed that dropped the calls already served still finds B.
    loaded = _one_trip(_stu(b"B", 2, 60), _stu(b"A", 3, 120))
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B", "A"], repeat=[0, 2], **{"from": 1, "to": 2})
    assert (hit["delay"], hit["dest_delay"]) == (60, 120)


def test_an_out_and_back_trip_takes_each_calls_own_update() -> None:
    # A B C B A: every stop but the turn (C) is called at twice.
    loaded = _one_trip(*(_stu(s, i, d) for i, (s, d) in enumerate(
        [(b"A", 0), (b"B", 10), (b"C", 20), (b"B", 30), (b"A", 40)], start=1)))
    cfg = _cfg("trip")
    hit = _answer(cfg, loaded, trip_id="T1", stops=["A", "B", "C", "B", "A"], repeat=[0, 1, 3, 4], **{"from": 3, "to": 4})
    assert (hit["delay"], hit["dest_delay"]) == (30, 40)
    # A slice of the return leg alone, without the turn: still the return calls.
    hit = _answer(cfg, loaded, trip_id="T1", stops=["B", "A"], repeat=[0, 1], **{"from": 0, "to": 1})
    assert (hit["delay"], hit["dest_delay"]) == (10, 40)
    # A stop called three times: A X A X A, boarding at the second X.
    loaded = _one_trip(*(_stu(s, i, d) for i, (s, d) in enumerate(
        [(b"A", 0), (b"X", 10), (b"A", 20), (b"X", 30), (b"A", 40)], start=1)))
    hit = _answer(cfg, loaded, trip_id="T1", stops=["A", "X", "A", "X", "A"], repeat=[0, 1, 2, 3, 4], **{"from": 3, "to": 4})
    assert (hit["delay"], hit["dest_delay"]) == (30, 40)


def test_a_predicted_time_without_a_delay_is_answered() -> None:
    loaded = _one_trip(_stu(b"A", 1, 60), _stu(b"B", 2, when=NOW + 600))
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A", "B"], **{"from": 1})
    assert (hit["delay"], hit["time"], hit["approx"]) == (None, NOW + 600, False)
    # A glitch delay (seven hours) takes its time along.
    loaded = _one_trip(_stu(b"A", 1, 7 * 3600, when=NOW + 7 * 3600))
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["A"], **{"from": 0})
    assert hit is None or hit["time"] is None


def test_deleted_trip_is_canceled() -> None:
    loaded = _one_trip(_stu(b"S1", 1, 60), trip_rel=7)
    hit = _answer(_cfg("trip"), loaded, trip_id="T1", stops=["S1"], **{"from": 0})
    assert hit["canceled"] is True and hit["delay"] is None


def test_a_feed_with_too_many_stop_updates_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(realtime, "MAX_FEED_STOP_UPDATES", 3)
    with pytest.raises(realtime.RtDecodeError):
        _one_trip(*(_stu(b"S", i, 0) for i in range(1, 5)))
    assert len(_one_trip(*(_stu(b"S", i, 0) for i in range(1, 4)))[0][1].trip_updates["T1"].stops) == 3


def test_a_stalled_feed_is_not_live() -> None:
    feed = _one_trip(_stu(b"S1", 1, 60), timestamp=NOW - realtime.MAX_FEED_AGE - 1)[0][1]
    assert realtime._fresh(feed, NOW) is None
    assert realtime._fresh(feed, NOW - 2) is feed
    feed.timestamp = None
    assert realtime._fresh(feed, NOW) is feed


# ---------------------------------------------------------------- the service


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _call(hass: HomeAssistant, data: dict) -> dict:
    return await hass.services.async_call(DOMAIN, SERVICE_REALTIME, data, blocking=True, return_response=True)


def _mock_szfv(aioclient_mock, **kw) -> None:
    aioclient_mock.get(SZFV + "trip-updates.pb", content=_pb("volan-szekesfehervar-trip-updates"), **kw)
    aioclient_mock.get(SZFV + "vehicle-positions.pb", content=_pb("volan-szekesfehervar-vehicle-positions"), **kw)


SZFV_REQ = {
    "feed": "volan-szekesfehervar",
    "trips": [
        {
            "key": "a",
            "trip_id": "hkir_H6112_607-1",
            "date": "20261102",
            "stops": ["hkir_666060", "hkir_665955", "hkir_666225"],
            "repeat": [0],
            "from": 1,
            "to": 2,
        },
        {"key": "missing", "trip_id": "hkir_nope"},
    ],
}


async def test_service_answers_and_shares_one_fetch_per_interval(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    _mock_szfv(aioclient_mock)
    resp = await _call(hass, SZFV_REQ)
    assert resp["feed"] == "volan-szekesfehervar"
    assert resp["updated"] == 1793605797
    assert set(resp["trips"]) == {"a"}
    assert resp["trips"]["a"]["delay"] == 190
    assert resp["trips"]["a"]["dest_delay"] == 181
    assert aioclient_mock.call_count == 2
    for _method, _url, _data, headers in aioclient_mock.mock_calls:
        assert "github.com/bator/hungarian-transport" in headers["User-Agent"]
    # Every card asking within the interval shares the copy.
    await _call(hass, SZFV_REQ)
    freezer.tick(59)
    await _call(hass, SZFV_REQ)
    assert aioclient_mock.call_count == 2
    freezer.tick(2)
    await _call(hass, SZFV_REQ)
    assert aioclient_mock.call_count == 4


async def test_conditional_requests_keep_the_cached_feed(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    _mock_szfv(aioclient_mock, headers={"ETag": '"abc"', "Last-Modified": "Mon, 02 Nov 2026 07:49:00 GMT"})
    await _call(hass, SZFV_REQ)
    aioclient_mock.clear_requests()
    aioclient_mock.get(SZFV + "trip-updates.pb", status=304)
    aioclient_mock.get(SZFV + "vehicle-positions.pb", status=304)
    freezer.tick(61)
    resp = await _call(hass, SZFV_REQ)
    assert resp["trips"]["a"]["delay"] == 190
    for _method, _url, _data, headers in aioclient_mock.mock_calls:
        assert headers["If-None-Match"] == '"abc"'
        assert headers["If-Modified-Since"] == "Mon, 02 Nov 2026 07:49:00 GMT"


async def test_outage_is_logged_once_and_backs_off(hass, aioclient_mock, freezer, caplog) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    aioclient_mock.get(SZFV + "trip-updates.pb", status=502)
    aioclient_mock.get(SZFV + "vehicle-positions.pb", status=502)
    resp = await _call(hass, SZFV_REQ)
    assert resp == {"feed": "volan-szekesfehervar", "trips": {}, "error": "rt_unavailable"}
    await _call(hass, SZFV_REQ)
    assert aioclient_mock.call_count == 2
    freezer.tick(realtime.FAIL_BACKOFF + 1)
    await _call(hass, SZFV_REQ)
    assert aioclient_mock.call_count == 4
    assert caplog.text.count("unavailable") == 2  # one line per feed URL
    aioclient_mock.clear_requests()
    _mock_szfv(aioclient_mock)
    freezer.tick(realtime.FAIL_BACKOFF + 1)
    with caplog.at_level(logging.INFO):
        resp = await _call(hass, SZFV_REQ)
    assert resp["trips"]["a"]["delay"] == 190
    assert caplog.text.count("answers again") == 2


async def test_route_start_matches_while_the_positions_feed_is_down(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    vbusz = J + "VBUSZ/"
    aioclient_mock.get(vbusz + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(vbusz + "vehicle-positions.pb", content=_pb("veszprem-vehicle-positions"))
    req = {"feed": "veszprem", "trips": [{"key": "a", "route": "5", "start": 525, "stops": ["SP1825"], "from": 0}]}
    resp = await _call(hass, req)
    assert resp["trips"]["a"]["delay"] == 19 and resp["trips"]["a"]["vehicle"] is not None
    # The trip updates carry no route_id: the one the vehicle had still names it.
    aioclient_mock.clear_requests()
    aioclient_mock.get(vbusz + "trip-updates.pb", content=_pb("veszprem-trip-updates"))
    aioclient_mock.get(vbusz + "vehicle-positions.pb", status=503)
    freezer.tick(realtime.DEFAULT_INTERVAL + 1)
    resp = await _call(hass, req)
    assert aioclient_mock.call_count == 2
    hit = resp["trips"]["a"]
    assert (hit["trip_id"], hit["delay"], hit["vehicle"]) == ("1140_1446_084500_A_HP", 19, None)


async def test_garbage_and_oversized_feeds_are_outages(hass, aioclient_mock, freezer, monkeypatch) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    monkeypatch.setattr(realtime, "MAX_FEED_BYTES", 1000)
    aioclient_mock.get(SZFV + "trip-updates.pb", content=_pb("volan-szekesfehervar-trip-updates"))
    aioclient_mock.get(SZFV + "vehicle-positions.pb", content=b"<html>proxy error</html>")
    resp = await _call(hass, SZFV_REQ)
    assert resp["error"] == "rt_unavailable"


async def test_unknown_feed_is_no_feed_without_a_request(hass, aioclient_mock) -> None:
    await _setup(hass)
    resp = await _call(hass, {"feed": "kecskemet", "trips": [{"key": "a", "trip_id": "x"}]})
    assert resp == {"feed": "kecskemet", "trips": {}, "error": "rt_no_feed"}
    assert aioclient_mock.call_count == 0


async def test_mav_matches_by_train_number(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
    aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
    aioclient_mock.get(MAV_STATIC, content=(FIX / "mav-static.zip").read_bytes())
    # GYSEV down: MÁV still answers.
    aioclient_mock.get(J + "1_134/trip-updates.pb", status=503)
    aioclient_mock.get(J + "1_134/vehicle-positions.pb", status=503)
    aioclient_mock.get(GYSEV_STATIC, status=503)
    req = {"feed": "mav", "trips": [{"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}]}
    resp = await _call(hass, req)
    hit = resp["trips"]["ic"]
    assert (hit["trip_id"], hit["delay"], hit["dest_delay"]) == ("35700507", 0, 600)
    assert hit["vehicle"]["lat"] == pytest.approx(46.50523, abs=1e-5)
    # The static feed is asked again only after STATIC_INTERVAL.
    freezer.tick(120)
    await _call(hass, req)
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == MAV_STATIC) == 1


async def test_a_failed_static_refresh_keeps_the_train_numbers(hass, aioclient_mock, freezer, monkeypatch) -> None:
    freezer.move_to(FROZEN)
    # Short enough for the fixtures' headers to stay fresh.
    monkeypatch.setattr(realtime, "STATIC_INTERVAL", 200)
    monkeypatch.setattr(realtime, "STATIC_FAIL_BACKOFF", 300)
    await _setup(hass)

    def mock(static_status: int) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
        aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
        if static_status == 200:
            aioclient_mock.get(MAV_STATIC, content=(FIX / "mav-static.zip").read_bytes(), headers={"ETag": '"z1"'})
        else:
            aioclient_mock.get(MAV_STATIC, status=static_status)
        for path in ("1_134/trip-updates.pb", "1_134/vehicle-positions.pb"):
            aioclient_mock.get(J + path, status=503)
        aioclient_mock.get(GYSEV_STATIC, status=503)

    def static_calls() -> list:
        return [c for c in aioclient_mock.mock_calls if str(c[1]) == MAV_STATIC]

    req = {"feed": "mav", "trips": [{"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}]}
    mock(200)
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    # The periodic refresh fails: the index from before still matches the
    # live feeds, which are fine.
    mock(503)
    freezer.tick(realtime.STATIC_INTERVAL + 1)
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    assert len(static_calls()) == 1
    # Asked again after STATIC_FAIL_BACKOFF, not every minute, and
    # conditionally: the copy kept is the one the ETag names.
    freezer.tick(realtime.DEFAULT_INTERVAL + 1)
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    assert len(static_calls()) == 1
    mock(304)
    freezer.tick(realtime.STATIC_FAIL_BACKOFF)
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    (call,) = static_calls()
    assert call[3]["If-None-Match"] == '"z1"'


async def test_a_bad_static_body_does_not_take_the_kept_copys_validators(
    hass, aioclient_mock, freezer, monkeypatch, caplog
) -> None:
    """A 200 whose body does not parse leaves the copy from before, and with
    it the validators that name that copy: the retry is not answered 304 for
    the bad body, nor logged as answering again."""
    freezer.move_to(FROZEN)
    monkeypatch.setattr(realtime, "STATIC_INTERVAL", 200)
    monkeypatch.setattr(realtime, "STATIC_FAIL_BACKOFF", 300)
    await _setup(hass)

    def mock(static: bytes, etag: str) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
        aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
        aioclient_mock.get(MAV_STATIC, content=static, headers={"ETag": etag, "Last-Modified": etag})
        for path in ("1_134/trip-updates.pb", "1_134/vehicle-positions.pb"):
            aioclient_mock.get(J + path, status=503)
        aioclient_mock.get(GYSEV_STATIC, status=503)

    req = {"feed": "mav", "trips": [{"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}]}
    mock((FIX / "mav-static.zip").read_bytes(), '"z1"')
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    mock(_corrupt_deflate(), '"z2"')
    freezer.tick(realtime.STATIC_INTERVAL + 1)
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600

    mock(_corrupt_deflate(), '"z2"')
    freezer.tick(realtime.STATIC_FAIL_BACKOFF + 1)
    caplog.clear()
    assert (await _call(hass, req))["trips"]["ic"]["dest_delay"] == 600
    (call,) = [c for c in aioclient_mock.mock_calls if str(c[1]) == MAV_STATIC]
    assert call[3]["If-None-Match"] == '"z1"' and call[3]["If-Modified-Since"] == '"z1"'
    assert not [r for r in caplog.records if "answers again" in r.getMessage()]


@pytest.mark.parametrize(
    "data",
    [
        {"feed": "Volan", "trips": []},
        {"feed": "volan", "trips": [{"key": "a"}] * (realtime.MAX_TRIPS + 1)},
        {"feed": "volan", "trips": [{"trip_id": "x"}]},
        {"feed": "volan", "trips": [{"key": "a", "stops": ["s"] * (realtime.MAX_STOPS + 1)}]},
        {"feed": "volan", "trips": [{"key": "a", "start": 9999}]},
        {"feed": "volan", "trips": [{"key": "a", "date": "2026-11-02"}]},
        {"feed": "volan", "trips": [{"key": "a", "train": "IC 822"}]},
        {"feed": "volan", "trips": [{"key": "a", "trip_id": "x" * 200}]},
        {"feed": "volan", "trips": [{"key": "a", "from": realtime.MAX_STOPS}]},
        {"feed": "volan", "trips": [{"key": "a", "repeat": [realtime.MAX_STOPS]}]},
        {"feed": "volan", "trips": [{"key": "a", "repeat": ["x"]}]},
    ],
)
async def test_service_validates_input(hass, aioclient_mock, data) -> None:
    await _setup(hass)
    with pytest.raises(vol.Invalid):
        await _call(hass, data)
    assert aioclient_mock.call_count == 0


async def test_service_refuses_while_the_entry_is_unloaded(hass, aioclient_mock) -> None:
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await _call(hass, SZFV_REQ)
    assert err.value.translation_key == "not_loaded"
    assert aioclient_mock.call_count == 0


async def test_fetches_are_rate_limited(aioclient_mock, freezer, caplog) -> None:
    """A loop over every feed id must not become a stream of requests."""
    freezer.move_to(FROZEN)
    feeds = {
        f"f{i}": realtime.FeedConfig(
            f"f{i}", (realtime.Source(f"https://x.test/{i}/tu.pb", "", "", ""),), frozenset({"trip"}), 60, 60
        )
        for i in range(realtime.FETCH_BURST + 5)
    }
    for i in range(realtime.FETCH_BURST + 5):
        aioclient_mock.get(f"https://x.test/{i}/tu.pb", content=_pb("volan-trip-updates"))
    session = aioclient_mock.create_session(asyncio.get_running_loop())
    hub = realtime.RealtimeHub(session, feeds=feeds)
    answers = [await hub.query(fid, []) for fid in feeds]
    await session.close()
    assert aioclient_mock.call_count == realtime.FETCH_BURST
    assert sum(1 for a in answers if a.get("error") == "rt_unavailable") == 5
    assert "rate limit" in caplog.text


def test_error_codes_are_documented() -> None:
    pkg = Path(integration.__file__).parent
    codes = set(re.findall(r'"error": "(rt_\w+)"', (pkg / "realtime.py").read_text(encoding="utf-8")))
    assert codes == {"rt_no_feed", "rt_unavailable"}
    for lang in ("en", "hu"):
        strings = json.loads((pkg / "translations" / f"{lang}.json").read_text(encoding="utf-8"))
        for code in codes:
            assert code in strings["services"]["realtime"]["description"], (lang, code)


def test_static_zip_member_cap(monkeypatch) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("trips.txt", "trip_id,trip_short_name\n" + "1:1,SZ 1\n" * 100)
    monkeypatch.setattr(realtime, "MAX_STATIC_MEMBER", 50)
    with pytest.raises(ValueError):
        realtime.train_numbers_from_static(buf.getvalue(), "1:")


def _zip_with_method(method: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trips.txt", "trip_id,trip_short_name\n" + "1:1,SZ 4510\n" * 50)
    data = bytearray(buf.getvalue())
    # Compression method in the central directory entry (offset 10).
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 10 : cd + 12] = method.to_bytes(2, "little")
    return bytes(data)


def _corrupt_deflate() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("trips.txt", "trip_id,trip_short_name\n" + "1:1,SZ 4510\n" * 50)
    data = bytearray(buf.getvalue())
    start = 30 + len("trips.txt")  # first local header, no extra field
    data[start : start + 8] = b"\xff" * 8
    return bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        _corrupt_deflate(),
        _zip_with_method(9),  # deflate64: NotImplementedError
    ],
    ids=["corrupt-deflate", "deflate64"],
)
def test_unreadable_static_zip_is_a_value_error(data: bytes) -> None:
    with pytest.raises(ValueError, match="unreadable static GTFS"):
        realtime.train_numbers_from_static(data, "1:")


def test_static_csv_error_is_a_value_error() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("trips.txt", "trip_id,trip_short_name\n1:1,\"" + "x" * 200_000 + "\"\n")
    with pytest.raises(ValueError, match="unreadable static GTFS"):
        realtime.train_numbers_from_static(buf.getvalue(), "1:")


async def test_unreadable_static_zip_is_an_outage_that_backs_off(hass, aioclient_mock, freezer) -> None:
    freezer.move_to(FROZEN)
    await _setup(hass)
    aioclient_mock.get(J + "1_198/trip-updates.pb", content=_pb("mav-trip-updates"))
    aioclient_mock.get(J + "1_198/vehicle-positions.pb", content=_pb("mav-vehicle-positions"))
    aioclient_mock.get(MAV_STATIC, content=_corrupt_deflate())
    aioclient_mock.get(J + "1_134/trip-updates.pb", status=503)
    aioclient_mock.get(J + "1_134/vehicle-positions.pb", status=503)
    aioclient_mock.get(GYSEV_STATIC, status=503)
    req = {"feed": "mav", "trips": [{"key": "ic", "train": "822", "stops": ["005501024", "005506189"], "from": 0, "to": 1}]}
    resp = await _call(hass, req)
    assert resp["feed"] == "mav"
    await _call(hass, req)
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == MAV_STATIC) == 1
