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

For the Volán, Volánbusz local, Paks and Debrecen cards
the **primary source is mavplusz.hu**, MÁV's own journey planner, asked for
just the trips the card shows (see
[mavplusz.hu first](#mavpluszhu-first-jbbghsqde-as-backup)). The
GTFS-Realtime feeds below are its **backup** for those operators, and the
only source for Veszprém and Miskolc (their cards' trip ids are not the
planner's), the MÁV trains, GYSEV and Orosháza.

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
| Miskolc, MVK (`miskolc`) | `jbb.ghsq.de/gtfs/hu/mvkzrt/` | route + start | 29 of 40; the delay is the vehicle's current one, none without the vehicle |
| MÁV trains (`mav`) | `jbb.ghsq.de/gtfs/hu/1_198/`, `…/1_134/` (GYSEV) | train number | 137 of 151 running trains |
| GYSEV as its own feed (`gysev`) | `jbb.ghsq.de/gtfs/hu/1_134/` | trip_id, else train number | — |

The table is data: `custom_components/hungarian_transport/realtime_feeds.json`
(static operator id → feed URLs, the feed's id prefix and how its trips are
matched). An operator that is not in it is simply not asked about. Kecskemét,
Pécs, Szeged (SZKT), Szombathely, Tatabánya, Kaposvár and BAHART have paths
on the server, but they were empty when this was written.

### mavplusz.hu first, jbb.ghsq.de as backup

`mavplusz.hu` runs OpenTripPlanner 2 with the timetables and realtime
updates of the operators above; the `jbb.ghsq.de` feeds are exported from
it, so its ids are the feeds' ids with their prefix kept (feed trip
`hkir_2036_1-3` of the `hkir:` feed is the planner's `hkir:hkir_2036_1-3`,
its stops likewise). Sources marked `"mavplusz": true` in
`realtime_feeds.json` (all 62 with the prefixes `hkir:`, `paks:` and
`debrecen:`; not `VBUSZ:` and `mvkzrt:`, whose cards send their own static
trip ids) are read this way:

- **What is asked.** Only requests with a `trip_id`, on a feed matched by
  trip_id. One GraphQL request (`POST
  https://mavplusz.hu/otp2-backend/otp/routers/default/index/graphql`, no key)
  per action call, for the trips of that call not cached yet: per trip its
  route, its calls on the service date (the card's `date`, else today in
  Budapest) with realtime delays and times, and the positions of the
  vehicles on its pattern, of which only the one on this trip is kept. The
  ids go in as GraphQL variables; vehicle ids (plate numbers) are not
  asked for. At most 40 trips a request: the action takes at most 40 trips
  a call, so one request always covers it (a longer list, possible only
  when the hub is called directly, is cut, not split). 20 trips take
  0.2–0.5 s and about 5 KiB gzipped, against 1.9 MB of trip updates for the
  Volán intercity feed. On 2026-11-14, 25 of 25 sampled feed trips were in
  the planner with delays within 60 s of the feed's, and their vehicles 20
  of 20.
- **What it answers.** A call without realtime data has no delay (the
  planner's scheduled time is no prediction). Every call CANCELED is a
  canceled trip, one CANCELED call among updated ones a skipped stop. A
  trip with no realtime call on the date has no update and no vehicle (the
  pattern's vehicle is then another day's run of it). The answer has the
  same shape as from the feeds; `updated` is the time of the planner's
  answer, or the newest feed header when a feed was read too.
- **When the feeds are read instead.** For a source whose planner request
  fails (an HTTP error, a timeout, an answer that is not JSON, any GraphQL
  error), is refused by the integration's rate limit, or falls in the
  minute's backoff after a failure; and when the planner knows **none** of
  the requested trips (a null answer for every one: ids the card's
  timetable does not share with the planner). A trip the planner does not
  know among ones it does is simply not answered: reading the 1.9 MB feeds
  every minute for it would undo the point, and the feeds, exported from
  the planner, would not know it either. A trip the planner knows but has
  no realtime data for is no reason to read the feeds either. Requests
  without a `trip_id` (route + start) and trains are always matched on the
  feeds; when such a request comes with trip ids in the same call, both
  are read and each trip id takes the planner's answer.
- **Caching.** Each trip's answer, a miss included, is kept per trip and
  service date for the feed's `interval` (60 s), shared by every card and
  browser; one request at a time.

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
  the card's stops, else neither is used. Only the positions feed names the
  route, in each vehicle; the trip updates do not. The integration remembers
  the route of each trip id's pattern (`1140_1446` of
  `1140_1446_084500_A_HP`) from the vehicles it has seen, and a trip update
  without a route takes it from there: a trip whose vehicle is not on the
  map, or every trip while the positions feed is down, still matches. A
  pattern seen on two routes is used for neither. Nothing is remembered
  until the positions feed has answered once since Home Assistant started:
  if it has been down since then, route + start finds no trip update and
  those cards keep the timetable (Debrecen's trip ids still match).
  Veszprém's stop ids are the card's, so its delay is the one at your stop;
  Miskolc's are not, so its delay is the vehicle's current one: the update
  at the stop the vehicle is at. A Miskolc trip without its vehicle (the
  positions feed down or stale, or the vehicle not in it) gets no delay and
  keeps the timetable and the delay the card remembers. Its trip update
  alone cannot say where the bus is, since it reports 0 for every stop
  already served. A cancellation still comes through.
- **Train number.** The MÁV feed's trip ids are not the MÁV GTFS's
  (`1:36166415` against `28178227_2158.`), and its trip updates carry no
  train number. The integration reads the train numbers from the same
  server's static MÁV and GYSEV GTFS (`trip_short_name`, "SZ 4510"; 1.4 MB
  and 0.1 MB, refreshed every 12 hours with a conditional request) and the
  station codes from the stop ids (`005501024_5` is a platform of
  Budapest-Kelenföld, 005501024). A refresh that fails, or a zip that does
  not read, keeps the train numbers from before: they still match the live
  feeds. It is asked again after 15 minutes, conditionally for the copy
  kept, not for the zip that did not read.

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
- **mavplusz.hu.** MÁV's legal notice
  ([mavcsoport.hu/mav-csoport/bemutatkozas/jogi-utmutatas](https://www.mavcsoport.hu/mav-csoport/bemutatkozas/jogi-utmutatas)),
  which covers mav.hu and its subdomains and mavplusz.hu, requires MÁV's
  prior written permission for downloading or reusing its content. The
  planner's GraphQL endpoint states no terms of its own and asks for no
  key. The integration asks it only for the trips a card shows, while the
  card is shown, at most once a minute per trip; it keeps nothing beyond
  that minute's in-memory cache and republishes nothing. Removing the
  `"mavplusz": true` keys from `realtime_feeds.json` turns this source off:
  the cards then read only the feeds, as before 1.10.19.
- **Not redistributed.** The integration does not republish any feed or
  planner answer: each Home Assistant fetches it for its own cards, keeps
  it in memory only, and shows the derived delay and position.

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
- **The planner.** One request per action call at most, for the trips not
  cached (each kept for the feed's interval), counted against the same
  rate limit as the feeds; JSON in, gzip, the same User-Agent, no
  redirects, a 20-second timeout, 4 MB per answer at most.
- **Outages.** A failing feed is not asked again for a minute (a static zip
  for 15 minutes), is logged once when it starts failing and once when it
  answers again, and the cards keep the timetable meanwhile (a static zip's
  train numbers from before stay in use, see above). A failing planner
  likewise: a minute's backoff, one warning, one line when it answers
  again, and the feeds meanwhile.

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

The requests carry no personal data: the User-Agent names the project, not
the installation. A feed is asked for as a whole, not for the trips a card
shows; the planner is asked for the trips a card shows (trip ids and a
date, nothing else), so mavplusz.hu can tell which timetable rows some
Home Assistant displays, as it can for anyone using its own site. The
vehicles' plate numbers in the feeds are not passed on to the cards, and
are not asked of the planner. `tests/fixtures/realtime/mavplusz_trips.json`
is a real planner answer of 2026-11-14 for four trips, trimmed. The test fixtures (`tests/fixtures/realtime/`) are real feeds of
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

- mavplusz.hu answers trip_id requests only. Route + start (Veszprém,
  Miskolc) and train numbers (MÁV, GYSEV) are matched on the feeds alone.
  The Veszprém and Miskolc cards' trip ids are not the planner's, so for
  them the planner answers none of the trips and the feeds are read, as
  before; the planner request (one a minute, cached misses) is the cost of
  the opt-in there.

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
