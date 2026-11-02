# Live delays and positions outside Budapest (GTFS-Realtime)

Volán coaches, the Volánbusz local networks, several city networks and MÁV
trains outside BKK FUTÁR's area used to show only the timetable: the row's
time as printed, and on the map a position estimated from the timetable. For
the operators below the integration now reads their public **GTFS-Realtime**
feeds, and the cards show the reported delay on the row (the same red or
green time as a BKK live delay) and the vehicle's reported position on the
map (**Live position** instead of **Estimated position (timetable)**).

When a feed has nothing for a trip, the card falls back to the timetable,
exactly as before.

## Sources

The feeds are the ones Transitous lists in
[`feeds/hu.json`](https://github.com/public-transport/transitous/blob/main/feeds/hu.json).
Most of them are exported from MÁV's journey planner (OpenTripPlanner) by
Jonah Brüchert and served from his server at `jbb.ghsq.de`:

| Operator in the card | Feed | Matched by | Live match rate, 2026-11-02 09:23 |
| --- | --- | --- | --- |
| Volán coaches (`volan`) | `jbb.ghsq.de/gtfs/hu/hkir/` | trip_id | 835 of 857 running trips (97 %), all with a vehicle |
| Volánbusz local networks (59, `volan-*`) | `jbb.ghsq.de/gtfs/hu/hkir_hkir_V-<KSH code>/` | trip_id | every feed trip and stop id is in the Volán GTFS (Székesfehérvár, Szeged, Győr checked) |
| Paks (`paks`) | `jbb.ghsq.de/gtfs/hu/paks/` | trip_id | 4 of 4 |
| Debrecen, DKV (`debrecen`, alias `dkv`) | `jbb.ghsq.de/gtfs/hu/debrecen/` | trip_id, else route + start | 56 of 56 trip ids are in menetbrand's `debrecen` GTFS |
| Orosháza (`oroshaza`) | `gtfs.gpspositions.net/storage/realtime/OROSH_V_ONK/` | trip_id | 1 of 1 (its own GTFS) |
| Veszprém, V-Busz (`veszprem`) | `jbb.ghsq.de/gtfs/hu/VBUSZ/` | route + start | 10 of 11 |
| Miskolc, MVK (`miskolc`) | `jbb.ghsq.de/gtfs/hu/mvkzrt/` | route + start | 29 of 40; the delay is the vehicle's current one |
| MÁV trains (`mav`) | `jbb.ghsq.de/gtfs/hu/1_198/`, `…/1_134/` (GYSEV) | train number | 137 of 151 running trains |
| GYSEV as its own feed (`gysev`) | `jbb.ghsq.de/gtfs/hu/1_134/` | trip_id, else train number | — |

The table is data: `custom_components/hungarian_transport/realtime_feeds.json`
(static operator id → feed URLs, the feed's id prefix and how its trips are
matched). An operator that is not in it is simply not asked about. Kecskemét,
Pécs, Szeged (SZKT), Szombathely, Tatabánya, Kaposvár and BAHART have paths
on the server, but they were empty when this was written.

### How trips are matched

- **trip_id.** The Volán, Paks, Debrecen and Orosháza feeds name trips and
  stops with the ids of the static GTFS the card's timetable is built from
  (`hkir_1010_10-5`, stop `hkir_109755_2` of station `hkir_109755`). The
  release indexes now carry each trip's `trip_id` as the seventh element of
  its row, so the card sends it and the integration looks it up. An index
  built before this change has no trip ids: those cards keep the timetable
  until the next timetable release.
- **route + start.** The Veszprém and Miskolc feeds use other trip ids than
  the GTFS the card ships, but their ids carry the start time
  (`1140_1446_084500_A_HP` starts at 08:45) and their `route_id` is the line
  number. The card sends the line and its trip's first time; two trips of a
  line starting at the same minute (both ends of the line) are told apart by
  the card's stops, else neither is used. Veszprém's stop ids are the card's,
  so its delay is the one at your stop; Miskolc's are not, so its delay is
  the vehicle's current one.
- **Train number.** The MÁV feed's trip ids are not the MÁV GTFS's
  (`1:36166415` against `28178227_2158.`), and its trip updates carry no
  train number. The integration reads the train numbers from the same
  server's static MÁV and GYSEV GTFS (`trip_short_name`, "SZ 4510"; 1.4 MB
  and 0.1 MB, refreshed every 12 hours with a conditional request) and the
  station codes from the stop ids (`005501024_5` is a platform of
  Budapest-Kelenföld, 005501024).

A stop without its own update takes the nearest earlier stop's delay
(GTFS-Realtime delays propagate down the trip). Delays more than 20 minutes
early or six hours late are dropped as feed glitches (a vehicle logged on to
the wrong trip reports -85 minutes on every stop), and a position older than
15 minutes is not shown.

### Precedence

Live data the card already has stays: BKK FUTÁR's own predictions and
vehicles, ELVIRA's delays, the `bkk_stop` companion's coach positions and
the EMMA train positions from its sensors. The feed fills in rows that have
none.

## Licence and terms

- **The server.** `jbb.ghsq.de` states no licence or terms for its GTFS and
  GTFS-Realtime files (the site's text is CC BY-SA 4.0; nothing about the
  data), and no rate limit. It is one person's server: on 2026-11-01 its
  owner moved Transitous's own polling behind Transitous's feed proxy with
  the commit message "Reduce load on my poor server". The integration
  therefore asks as little as it can (below) and names itself in every
  request. Contact: Jonah Brüchert, `jbb at kaidan dot im`, or
  [@jbb@social.tchncs.de](https://social.tchncs.de/@jbb).
- **The data.** The feeds are exports of MÁV-Volán's public journey planner.
  MÁV hands out its static GTFS free of charge after a registration
  ([mavcsoport.hu/gtfs-igenybejelento](https://www.mavcsoport.hu/gtfs-igenybejelento));
  the request page names no licence or terms for redistribution or for
  real-time data. Transitous lists MÁV's own feed as CC0-1.0. Orosháza's feed
  (`gtfs.gpspositions.net`) also states no terms.
- **Not redistributed.** The integration does not republish any feed: each
  Home Assistant fetches it for its own cards, keeps it in memory only, and
  shows the derived delay and position.

Should either side object, removing the operator from `realtime_feeds.json`
(or the whole file) turns the feature off without touching the cards.

## Polling

- **On demand only.** Nothing is fetched in the background. A card calls the
  `hungarian_transport.realtime` action as part of its normal refresh (every
  45 seconds by default), only while it is shown (a hidden browser tab
  stops polling), and only for the operator of its own rows.
- **Once a minute at most.** The feeds are regenerated about once a minute
  (vehicle positions every ~54 s, trip updates every ~60–70 s, measured on
  2026-11-02), so each feed is fetched at most every 60 seconds (never under
  30), the Volán intercity trip updates (~500 kB compressed, 1.9 MB
  decoded) every 120 seconds. Every card and browser of one Home Assistant
  shares the copy.
- **Polite requests.** A descriptive User-Agent with the project's address;
  gzip; `If-None-Match` / `If-Modified-Since` where the server sends an ETag
  or Last-Modified (`gtfs.gpspositions.net` and the static zips do; the
  `jbb.ghsq.de` feeds do not); no redirects; a 20-second timeout; 8 MB per
  feed and 30 MB per static zip at most; at most 30 fetches in a burst and
  one every two seconds after that, whatever the cards ask.
- **Outages.** A failing feed is not asked again for a minute (a static zip
  for 15 minutes), is logged once when it starts failing and once when it
  answers again, and the cards keep the timetable meanwhile.

## The action

`hungarian_transport.realtime` (response only; any logged-in user, like the
timetables the cards read anyway):

```yaml
action: hungarian_transport.realtime
data:
  feed: volan-szekesfehervar
  trips:
    - key: "1"
      trip_id: hkir_H6112_607-1
      date: "20261102"
      stops: [hkir_666060, hkir_665955, hkir_666225]
      from: 1
      to: 2
      repeat: []        # indices of stops the trip calls at more than once
```

```yaml
feed: volan-szekesfehervar
updated: 1793605797
trips:
  "1":
    trip_id: hkir_H6112_607-1
    delay: 190          # seconds, at stops[from]
    time: null          # predicted epoch, when the feed gives one
    dest_delay: 181     # at stops[to]
    dest_time: null
    approx: false       # true: the vehicle's current delay, not at your stop
    canceled: false     # the trip is CANCELED or DELETED in the feed
    skipped: false      # true: the trip passes stops[from] or stops[to] by
    vehicle: {lat: 47.19302, lon: 18.403648, bearing: 327.0, speed: 1.9, timestamp: 1793605748, stop_id: hkir_665955_2, status: 1}
```

A stop without an update of its own takes the nearest earlier stop's delay.
A stop the feed marks SKIPPED has no delay (`skipped`), and one marked
NO_DATA (or after a NO_DATA stop) has none either, not even the approximate
one. A stop a loop trip calls at twice (listed in `repeat`) takes its first
update after the previous stop's, or its first one when there is none after
it (a feed that dropped the calls already served); a stop the trip calls at
once sets the order. A stop with a predicted time but no delay answers
with `time` and a null `delay`. A feed whose
header timestamp is more than 15 minutes old is a stalled publisher, not live
data: it answers as unavailable.

A trip may also be asked by `route` and `start` (minutes after the service
day's midnight, up to 2880) or by `train` (digits). Up to 40 trips of up to
120 stops per call; anything longer is refused before a feed is touched.
When nothing can be looked up the answer has no trips and an `error`:
`rt_no_feed` (no feed for that operator) or `rt_unavailable` (the feed cannot
be reached now).

## Privacy

The request carries no personal data: the User-Agent names the project, not
the installation, and the feed is asked for as a whole, not for the trips a
card shows. The vehicles' plate numbers in the feeds are not passed on to
the cards. The test fixtures (`tests/fixtures/realtime/`) are real feeds of
2026-11-02 trimmed to a few trips, with the vehicle descriptors and entity
ids (plates) removed.

## Decoding

Home Assistant does not ship the `protobuf` package: from 2024.10 to 2026.2
it is only a version pin in `package_constraints.txt`, not a dependency. The
integration reads the fields it needs with a small wire-format decoder in
`realtime.py` (FeedHeader timestamp; TripUpdate trip, stop_time_update
delays and times, schedule relationships; VehiclePosition trip, position,
timestamp, current stop; Alert header and informed entities), run in the
executor: the Volán intercity trip updates (1,584 trips, 48,000 stop updates)
take about 0.3 s on a laptop.

## Limits

- Cards with an index from before this change (no trip ids) get live data
  for Veszprém, Miskolc and MÁV only, until the next timetable release.
- The weekly release's index can be a few days older than the live feed: a
  trip the operator renamed since has no live data until the next release
  (or the local daily timetable update).
- Debrecen and GYSEV as city operators depend on the static feed added
  elsewhere: mapped by the ids `debrecen` (menetbrand's slug; `dkv`, the
  Transitous name, is an alias) and `gysev`.
- A trip cancelled in the feed (`canceled`) or a stop it skips (`skipped`) is
  reported, but the card does not yet strike it through: it shows the
  timetable time there.
- The planner card does not use the feeds yet.
