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

For the MÁV and GYSEV trains and the Volán, Volánbusz local, Paks,
Debrecen, Veszprém and Miskolc cards the **primary source is mavplusz.hu**,
MÁV's own journey planner, asked for just the trips the card shows (see
[mavplusz.hu first](#mavpluszhu-first-jbbghsqde-as-backup)). The
GTFS-Realtime feeds below are its **backup** for those operators, and the
only source for Orosháza.

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
| Veszprém, V-Busz (`veszprem`) | `jbb.ghsq.de/gtfs/hu/VBUSZ/` | trip_id, else route + start (the card's trip ids are not the feed's: route + start in practice) | 10 of 11 |
| Miskolc, MVK (`miskolc`) | `jbb.ghsq.de/gtfs/hu/mvkzrt/` | trip_id, else route + start (the card's trip ids are not the feed's: route + start in practice) | 29 of 40; the delay is the vehicle's current one, none without the vehicle |
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
its stops likewise; MÁV trip `1:36166415` of the `1:` feeds is the
planner's `1:36166415`, Budapest-Keleti `1:005510017` with its platforms
`1:005510017_9`). Sources marked `"mavplusz": true` in
`realtime_feeds.json` (all 67 with the prefixes `hkir:`, `paks:`,
`debrecen:`, `VBUSZ:` and `mvkzrt:`, and MÁV's two and GYSEV's own with
`1:`) are read this way:

- **What is asked.** Requests with a `trip_id` on a feed matched by
  trip_id, requests with a `route` and `start` on a feed matched by
  route + start (Veszprém, Miskolc, Debrecen), and requests with a `train`
  on a feed matched by train number (`mav`, `gysev`). One GraphQL request (`POST
  https://mavplusz.hu/otp2-backend/otp/routers/default/index/graphql`, no key)
  per action call, for the trips of that call not cached yet: per trip its
  route, its calls on the service date (the card's `date`, else today in
  Budapest) with realtime delays and times, and the positions of the
  vehicles on its pattern. Almost every MÁV trip of the day sits on a
  realtime pattern of its own (`1:V3775_106::rt#2972`), not only the ones
  the realtime data changed (`MODIFIED`, `UPDATED`, with delay 0 too,
  checked on 2026-11-18 and 2026-11-19; other planner sources have some),
  and such a pattern lists no vehicle: its vehicle is on its route's
  scheduled patterns. So when a live trip's answer names such a pattern
  and its first call is at most 5 minutes ahead (the trains not yet
  departed had no vehicle on any pattern) and its last call at most 15
  minutes past (a finished run's id would take the next run's vehicle),
  its route's vehicles are asked right after, in a request of their own (`route(id:) { patterns {
  vehiclePositions } }`, one alias per route, at most 20), and the trip
  has its vehicle from its first answer on. That request never costs the
  delays: it is made after the trip request has let other cards' trip
  requests go, and has its own cap (256 KiB), timeout (5 s, and
  never past the card's round), token share (one every 10 s, burst 6,
  within the planner limit) and backoff (3 minutes; not after a timeout
  the round's deadline cut below the 5 s, which is no sign of an outage;
  a round cancelled before its request ran gives the token back); when it
  is not made or fails, the trips simply have no vehicle that round, logged once at
  debug level, and the planner is not backed off. Each route's answer
  size is remembered for three hours, reckoned from its patterns and
  vehicles (250 bytes a vehicle; the answer is not encoded again to weigh
  it): a request takes the routes that fit
  192 KiB by that reckoning (an unseen route counted as 8 KiB), and a
  route that alone went over the cap is left out until its size is
  forgotten (an alias the planner answered with an error teaches no
  size). One request per route: a round does not ask a route another
  round is asking, it waits for that answer (within its own round), and
  a route's answer gives its vehicles to every trip of the round on that
  route, those answered from the cache too. Only trips the round fetched
  start a vehicle request; a round of cached trips only waits. The same
  gates hold for every trip given a vehicle, also from another trip's
  route answer. Measured in the daytime on 2026-11-19: 207–326 bytes a
  vehicle, 1.75 kB a route at most, 12.5 kB for 17 MÁV routes together;
  the code does not depend on those sizes
  (`tests/fixtures/realtime/mavplusz_rt_pattern.json` is an evening
  answer). Each position names its trip, and is kept for that trip's live
  run whichever alias it came with. The
  ids go in as GraphQL variables; vehicle ids (plate numbers) are not
  asked for. At most 40 trips a request, of the ones not cached yet; the
  rest are cut, not split. What a row needs goes in whole or not at all (a
  route + start row's resolved trips, a train's trips), the least
  recently asked first, so every row gets its turn on a board of more; a
  row left for a later poll is answered from its last answer while that
  is at most two intervals past its expiry, else matched on the feeds
  until a later poll asks for it. 20 trips take
  0.2–0.5 s and about 5 KiB gzipped, against 1.9 MB of trip updates for the
  Volán intercity feed. On 2026-11-14, 25 of 25 sampled feed trips were in
  the planner with delays within 60 s of the feed's, and their vehicles 20
  of 20.
- **Route + start.** The planner's trip ids are the feeds' ones, start
  time included (`VBUSZ:1131_2084_073400_SZ` starts at 07:34), and its
  route ids are the prefix plus the card's route (`VBUSZ:2`, `mvkzrt:14`;
  checked live on 2026-11-14 for four lines of each). So a route + start
  request is first resolved to the planner's trips: one more request lists
  the trip ids of every asked route on the service date (`route(id:) {
  patterns { tripsForDate(serviceDate:) { gtfsId } } }`, at most 40 routes;
  about 40 bytes a trip, 8 routes about 16 kB), kept for three hours per route and date. The
  trips whose id starts at the card's minute (modulo a day, as on the
  feeds) are then asked for with the trip ids. Two trips of a line at the
  same minute (both ends of Miskolc line 14 at 07:10) are both asked for
  and told apart by the card's stops exactly as on the feeds, else neither
  is used; when only one of the two is in the planner's answer or the
  feeds, it is used only if it calls at the card's stops. The planner's
  trip updates count as ones whose route was learned from a vehicle, as
  the feeds' are, so a Miskolc trip without its vehicle still gets no
  delay; its delay is the vehicle's current one.
- **Train number.** The planner names a train's trips by the ids the
  feeds use (`tripShortName` is the train number, "3311 személyvonat"),
  but cannot be asked by number. So a train request is first resolved with
  the same static MÁV and GYSEV GTFS the feeds are matched with (see
  [Train number](#how-trips-are-matched) below): every trip id of the
  number, per source, is asked for today and for yesterday (Budapest), the
  two runs a train row without a date can mean, but for the trips that
  zip's calendar (`calendar.txt`, `calendar_dates.txt`) does not run that
  day. The planner lists a trip's scheduled calls on any day, run or not
  (`1:36777783` on 2030-01-05: 10 calls), so only the calendar tells a
  variant of another weekday from a run; a zip without one asks for every
  trip, and so does a day outside the first and last day its calendar
  names (a copy kept from an older download says nothing of today). A train's trips go in one request together (a number has 2 trips
  in the median, 12 at most). The answer is then matched exactly as on
  the feeds: today's run, else last night's; a number with several trips
  that day is told apart by the card's stations. Several calling there
  that give the card the same answer (within 30 s at its boarding and
  destination stop) are one train listed twice (741 ran as `1:36777783`,
  `UPDATED`, and `1:36391307`, `MODIFIED`, on 2026-11-18, the same calls
  and delays): the one with the vehicle answers, else the first by trip
  id. Ones that differ there are not answered. Both `UPDATED` and
  `MODIFIED` trips carry the delays. Last night's run is
  used only until 15 minutes after its last call: the planner keeps a
  finished run's realtime data for hours (runs that left Budapest-Keleti
  four hours earlier still had it, checked 2026-11-18), which would
  otherwise answer for a train whose run today has not started. A train
  is answered from the planner when it answered every one of those trips
  and knew one, or answered today's live run calling at the card's
  stations with only last night's runs missing (today's run is taken over
  last night's anyway); a train it has no answer for, or one whose
  numbers the static feed could not give (its download failing with no
  copy kept), is matched on the feeds as before, which are then read for
  its source. A number the static feed lacks reads no feed: the feeds
  could not match it either. One whose trips the calendar runs on neither
  day is not asked of the planner, but matched on the feeds, which never
  read the calendar (it may be wrong, or the train an added one). A
  source the call has no train of (GYSEV's for a MÁV train) is not read
  at all.
- **What it answers.** A call without realtime data has no delay (the
  planner's scheduled time is no prediction). Every call CANCELED is a
  canceled trip, one CANCELED call among updated ones a skipped stop. A
  trip with no realtime call on the date has no update and no vehicle (the
  pattern's vehicle is then another day's run of it). The answer has the
  same shape as from the feeds; `updated` is the time of the planner's
  answer, or the newest feed header when a feed was read too.
- **When the feeds are read instead.** For the trips of a source whose
  planner request fails, is refused by the integration's rate limit, or
  falls in the minute's backoff after a failure, but for the trips
  answered within their interval or still kept (see Caching): those stay
  the planner's. The planner failing (no answer, a timeout, HTTP 5xx, 429
  or 403, an answer that is not JSON) backs every planner request off for
  a minute, the BKK boards' too (see Outages). A request failing for its
  own sake (an answer over its cap, a GraphQL error not tied to one trip,
  another HTTP 4xx, an answer its decoding in the executor did not finish
  within the request's timeout) backs nothing off and is logged at debug
  level only. A request of one trip (or route list) holds that one back
  for a minute, matched on the feeds meanwhile, and for twice as long
  every time it fails alone again, up to 15 minutes (an answer for it
  starts that again at a minute); one of several holds none
  back: for the next 15 minutes each of its trips goes in a request of
  half as many trips at most, down to the one that fails it alone, which
  alone is held, and asked alone from then on for as long as it fails
  (each failure renews that for 15 minutes past its hold, so it never
  rejoins a full request to fail it again); a trip that had no answer
  lately goes after the ones never asked, so it never takes every round
  from them; another card's trips are asked as before. Six requests of a
  kind (trips, route lists: the route lists answering do not hide that
  every trip request fails) in a row failing so, for six trips or route
  lists at least, no answer of that kind in between, are no bad trip's
  but the planner refusing every request (a schema change it answers
  with a GraphQL error without a path, an HTTP 400 for any body): that is
  an outage from then on, one warning and the shared minute's backoff,
  until a request of that kind is answered. What those six split and held
  is undone then, and the outage's probes ask as many trips as ever, so
  the first request after the planner is back answers every trip (before,
  every trip was left at one a request for 15 minutes, a trip a poll
  answered). A trip of the wrong shape in an
  answer is that trip's failure alone (held for the minute), the others
  are answered. A bug of the integration's own reading of an answer (a
  `TypeError`, `KeyError` and the like, which no answer causes) fails its
  request as one failing for its own sake, so only the trip whose data
  meets the bug ends up held, and is logged as a warning with its
  traceback once an hour per kind of request (trips, route lists,
  vehicles) and place in the code, rather than moving all MÁV realtime to
  the feeds without a word, or warning again after every other answer
  read. It is never part of the six (the planner answered): a release
  whose reading code fails on every answer is that warning, not "Realtime
  source unavailable" and a backoff of the BKK boards too. When the call's planner round,
  waiting for other cards' requests and, after a restart, the static train
  numbers' download included, takes over 20 s (a request is begun only
  with at least 10 s of that left, so waiting alone is no outage; numbers
  not loaded by then leave the trains to the feeds, which wait for the
  same download). A GraphQL error the planner ties to one trip (its `path`
  begins with that trip's alias: a trip id or date it cannot serve) is no
  answer for that trip alone, matched on the feeds, and backs nothing
  off; so is a trip id the request cap left out, with no answer kept. And
  when the planner knows **none** of
  the requested trips (a null answer for every one: ids the card's
  timetable does not share with the planner). A trip the planner does not
  know among ones it does is simply not answered: reading the 1.9 MB feeds
  every minute for it would undo the point, and the feeds, exported from
  the planner, would not know it either. A trip the planner knows but has
  no realtime data for is no reason to read the feeds either. A route +
  start the planner does not resolve (a route it does not know, no trip at
  that minute, or the route list failing) is matched on the feeds, which
  are then read too; the route lists cached already still answer when the
  request for the others fails. A train likewise, as above.
- **Caching.** Each trip's answer is kept per trip and service date for
  the feed's `interval` (60 s), each route's trip list for three hours,
  shared by every card and browser; one trip or route-list request at a
  time (the vehicle requests go beside them). An answer that
  no longer changes is kept three hours too: a trip the planner does not
  know or lists without a call that day, a run without realtime data whose
  last call was over six hours ago (the most a delay is taken to be), and
  a run of an earlier service day over for 15 minutes, so last night's
  runs of the trains asked by number are asked once in a while, not every
  minute all day. At most 20,000 answers are kept, the least recently
  asked dropped first. On a board of 40 trains of two live trips each (80
  trips a minute against 40 a request) every train is answered from the
  planner on every poll once each was asked, and asked again every second
  poll at a 40 s poll (`test_a_big_board_is_answered_by_the_planner_poll_after_poll`).

### How trips are matched

- **trip_id.** The Volán, Paks, Debrecen and Orosháza feeds name trips and
  stops with the ids of the static GTFS the card's timetable is built from
  (`hkir_1010_10-5`, stop `hkir_109755_2` of station `hkir_109755`). The
  release indexes now carry each trip's `trip_id` as the seventh element of
  its row, so the card sends it and the integration looks it up. An index
  built before this change has no trip ids: those cards keep the timetable
  until they get a newer index (a release, or the weekly one from GitHub
  Pages).
- **route + start.** (The same rule on mavplusz.hu, see above.) The
  Veszprém and Miskolc feeds use other trip ids than the GTFS the card
  ships, but their ids carry the start time
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
  Budapest-Kelenföld, 005501024). The card sends no date for a train, so
  only a run of today or yesterday (Budapest; a night train still on its
  way) is matched. The zip's calendar is kept too: the planner is asked
  only for the trips that run that day. A refresh that fails, or a zip
  that does not read, keeps the train numbers from before: they still match the live
  feeds. It is asked again after 15 minutes, conditionally for the copy
  kept, not for the zip that did not read. The planner is asked for the
  trips of the same train numbers first (see
  [Train number](#mavpluszhu-first-jbbghsqde-as-backup) above), so the
  static zips are read whichever source answers.

A stop without its own update takes the nearest earlier stop's delay
(GTFS-Realtime delays propagate down the trip). Delays more than 20 minutes
early or six hours late are dropped as feed glitches (a vehicle logged on to
the wrong trip reports -85 minutes on every stop), and a position older than
15 minutes, or stamped more than 5 minutes ahead of now (it would never
grow old), is not shown. A predicted time more than two days from now is
no prediction, and a feed header's timestamp that far ahead is no
`updated`; from the planner, a service day outside 2000-2100, a time more
than a day before or two days after it, a delay over two days either way
and a vehicle timestamp outside those years are no number at all. So no
answer has a number Home Assistant's JSON cannot hold (past 64 bits),
which failed the whole action's response.

### Precedence

Live data the card already has stays: BKK FUTÁR's own predictions and
vehicles, ELVIRA's delays, the `bkk_stop` companion's coach positions and
the EMMA train positions from its sensors. The feed fills in rows that have
none.

## BKK boards when FUTÁR fails

A Budapest departure board is read from BKK FUTÁR first, then from
mavplusz.hu, then from Transitous. When the integration holds the FUTÁR key
and FUTÁR does not answer `arrivals-and-departures-for-stop.json` or
`trip-details.json` (an outage, a timeout, an HTTP error, FUTÁR's own HTTP
429, or a rejected key), the `hungarian_transport.futar` action answers from
the planner's BKK feed instead (`mavplusz_board.py`), in FUTÁR's own shape,
with `"source": "mavplusz"` at the top of the answer. The planner has
FUTÁR's ids with `BKK:` for `BKK_` (stop `BKK_F00940` is `BKK:F00940`). When
the planner cannot answer either, the action returns FUTÁR's error as
before, and the card fills the board from Transitous.

- **What the rows carry.** Departure time, the prediction for a live trip
  (none for a scheduled one: no delay shown), headsign, route number,
  colours and type, the trip's wheelchair and bicycle flags, booking, the
  pole of a station's departure and its platform code. Not the vehicle's
  GPS position and plate: the rows show no live marker. HÉV is not in the
  planner's BKK feed, so a HÉV board fails over to Transitous. A trip's
  details (where it goes, the ride's length) come from the planner too, on
  the service date the trip's board gave (FUTÁR's own board or the
  planner's); a trip no board dated is asked for today in Budapest, and
  before 05:00 for yesterday's service day as well, the run under way
  being the answer (a night bus after midnight). They carry the trip's
  shape as FUTÁR's do (`entry.polyline.points`, OTP2's `tripGeometry`, the
  same Google encoding of 1e5; checked on 2026-11-19), so the ride map
  draws the trip's own line. The card discovers no further routes from a
  planner board: their route details would go to the FUTÁR that just
  failed.
- **How much of a board.** The planner gives 60 departures a pattern over
  eight hours. A board is not cut to its earliest rows across every
  pattern (a hub's metro lines filled 80 rows within 22 minutes at
  Keleti, 42 at Deák tér, on 2026-11-19): each route and headsign keeps
  its earliest 80 rows (the card's widest page of one destination), and
  a card's answer, cut to its horizon, holds 400 rows at most (some
  160 kB), the patterns with the most rows giving way first past that.
  So a three-hour card, or one filtered to a sparse line's destination,
  still gets its rows.
- **Stops the planner lacks.** A stop (or station, or a station's poles)
  the planner answers null for is remembered for 10 minutes, and once it
  answered null twice in a row for 12 hours (one null can be the planner's
  own, a graph being reloaded): FUTÁR keeps its whole budget there and the
  planner is not asked again meanwhile (HÉV stops; the MÁV
  stations on FUTÁR's boards, `BKK_005510017` and `BKK_005501024` at
  Kelenföld, which the planner has only as MÁV's `1:005510017`: a MÁV
  board in FUTÁR's shape would carry MÁV trip and route ids the card's
  BKK follow-ups cannot use, so they are not mapped).
- **Stations.** A GTFS parent station (`BKK_CS056233`, Keleti's metro) is
  one planner stop whose board holds its child stops' departures. So are
  the 38 of FUTÁR's combined stations (`BKK_CSF…`) that BKK's GTFS has
  (`BKK_CSF00062`, Batthyány tér): the first board of such a station
  FUTÁR cannot give asks the planner for the station itself, and whether
  it knew it is kept as for a stop (12 hours, a null 10 minutes until it
  was two in a row). The others are FUTÁR's alone
  (`BKK_CSF01131`, the Keleti preset, and the parent station
  `BKK_CS007896`, the Kelenföld one, are null in the planner): their
  board is merged from their child poles' boards, the poles FUTÁR's own
  answers named (a station board's departures and references; for a
  combined station any answer's stop references too, not for a parent
  station, which nearly every stop has), at most 24 poles and 20
  departures a pattern each, for at most 512 stations. A combined station
  is asked by its poles first; a
  parent station (`BKK_CS…`) by itself first, by its poles once the
  planner said it does not know it. Those are kept in
  `.storage/hungarian_transport.bkk_stations` (written five minutes after
  a change, at once when the entry unloads or reloads, and when Home
  Assistant stops; removed with the integration, and never written again
  by a FUTÁR answer landing after that), so they have a fallback right
  after a restart during an outage too. Each pole and station carries
  when FUTÁR last named it (a station FUTÁR keeps naming is news for the
  store once a day, so its stored time is never more than a day old; a
  stored time in the future counts as the restore's): one not named for
  30 days goes, and at the
  limits the least recently named goes first (a record of before this,
  poles alone, counts as named at the update). Keleti has a seed until
  FUTÁR names its own: the 15 BKK surface stops the planner names
  "Keleti pályaudvar M" (and "… M (Garay utca)"), as on 2026-11-18. A
  FUTÁR-only station FUTÁR has named no pole of has no fallback. FUTÁR's
  ad-hoc trips (ids of ten or more digits) are not in the planner either.
- **Not asked when.** The integration's own FUTÁR rate limit, an error
  status FUTÁR gave for the request, and a request never sent are not
  outages; nor are search, route details, plans and timetables, which the
  planner does not answer. A rejected key still opens the reauthentication.
- **No key, no planner.** Without a FUTÁR key the action does not exist and
  the cards read Budapest from Transitous; the planner alone would not
  answer the searches and route patterns a card with the action asks for.
- **Limits.** One planner request per stop per 35 s for every card and
  horizon together (a request the limit or a backoff kept from being sent
  does not count; one sent that failed leaves FUTÁR its whole budget for
  that stop for those 35 s), while FUTÁR fails (the widest window, eight hours, is
  asked, 60 departures a pattern, and each card's horizon cut from it at
  the time it is given, so a cached board stays right for its timetable
  and its delays are at most 35 s old; the cards poll every 20 s at the
  earliest, 45 s by default, so a shorter time only shared one poll among
  the viewers asking at once; an answer of up to 2 MB, one of 512 kB or
  more decoded and made into rows in Home Assistant's executor, not on its
  event loop); the trip details asked within 50 ms go in one
  request of up to 40 trips, cached 60 s, a trip never waiting for an
  earlier request. These requests have a share of the integration's one
  planner limit (see [Polling](#polling)): 0.25 a second with a burst of
  15, within the total's 0.5 and 30. A call failing fast on a rejected key,
  or answered by the planner first in FUTÁR's outage, first takes a token
  of FUTÁR's own rate limit, as a call to FUTÁR would; one the planner
  cannot take after all (its share used up, or no answer) asks FUTÁR with
  that same token: one request, one token. The cards asking one board or
  trip at once in FUTÁR's outage share the planner asked first, one token
  of FUTÁR's and one of the planner's for them all, as they share one
  FUTÁR request outside it; a token taken for a request that was not sent
  after all (it joined one under way, or the key was rejected meanwhile)
  goes back. A board or trip details the planner holds (its 35 s and
  60 s caches, or a request under way) are answered from there without a
  planner token, also once the share is used up: a second card, horizon
  or browser asking a board the planner just gave does not wait for FUTÁR
  then.
- **Failures.** The planner failing (no answer, a timeout, HTTP 5xx, 429
  or 403, a page that is not JSON: a maintenance or Cloudflare page) is
  not asked for 30 s, by the realtime hub either, logged once. A
  request failing for its own sake (an answer over its cap, a GraphQL
  error, an answer of the wrong shape, another HTTP 4xx) is
  that stop's or those trips' alone: they are not asked for 10 minutes,
  nobody backs off, and it is logged at debug level only. A GraphQL error
  that names one trip of a batch (OTP's `DataFetchingException` of a bad
  id or date has the alias as its path), or one trip of the wrong shape,
  makes that trip no answer (not asked for 10 minutes) and leaves the
  others'. An answer decoded in the executor gets what is left of the
  request's timeout, and a second at least (the answer is waited for a
  second less than the timeout, so the whole stays within it): past it
  the stop is asked again after the board's 35 s, as when the planner
  times out, not held for 10 minutes, and nobody backs off. A bug of the
  integration's reading of an answer, a trip's details included, is that
  request's failure too, and a warning with its traceback once an hour
  per kind (boards, trip details) and place in the code, however many
  answers are read meanwhile.
- **FUTÁR's outage.** Once FUTÁR failed (and until it answers again), a
  board or trip details the planner can answer is asked of the planner
  first, at once, and FUTÁR is not waited for: before, every poll waited
  8 s for a FUTÁR that was down (18 s once the planner's share was used
  up), holding a planner token meanwhile. FUTÁR itself is asked by one
  request at most every 30 s (the probe; any other request sent to FUTÁR
  meanwhile, a search or route details, counts as one), as below: FUTÁR
  first, cut short, the planner after it. Its answer ends the outage, and
  every request asks FUTÁR first again. A board FUTÁR's own cache holds
  (answered within 15 s), or whose FUTÁR request is under way (the probe),
  is answered from it or joins it, as outside the outage, for no token.
  What the planner cannot answer (a
  stop it lacks, its share used up, no answer from it) asks FUTÁR as
  before, with what is left of the card's 20 s. So once the planner's BKK
  share is used up, every board and trip detail of every card goes to
  FUTÁR directly with its whole budget, as a search or route details does,
  and no probe is needed then (each of them is one). They are not capped
  beyond FUTÁR's own rate limit (5 a second, a burst of 400, a token waited
  for 4 s at most) and the card's own gate (4 to 24 calls under way per
  dashboard): until its budget runs out the integration cannot tell a slow
  FUTÁR from a hanging one, and a slow one (10 s a board) still answers
  them, which a cap on the calls under way would refuse.
- **Logged once, also when it flaps.** FUTÁR's outage is one warning when
  it starts failing and one info line when it answers again. FUTÁR failing
  again within 5 minutes of either line is not warned about at once: a
  FUTÁR down and up every few seconds logged a pair of lines every few
  seconds (58 of each in ten minutes). Such a failure is warned about once
  the 5 minutes have passed and it still stands (on the next request), and
  its end is said only then; one that ended before is the outage said
  before, still under way. The lines still alternate, a flapping FUTÁR logs
  a pair every 5 minutes at most, and an outage lasting that long is said
  5 minutes late at most. None of this waits for the log: the planner is
  asked first from the failure on, said or not, until FUTÁR answers.
- **The card's 20 s.** FUTÁR gets 8 s of the card's 20 s only while its
  outage stands (it failed and has not answered since; while it answers,
  no token is reserved and FUTÁR keeps its whole budget, and a request
  failing fast still falls back with a token taken then; the first
  request hanging for FUTÁR's whole budget gets no planner answer and
  starts the outage), only when the wait for FUTÁR's own token leaves
  room for both (wait + 8 s + 6 s within 18.5 s, else FUTÁR gets 18 s
  less the wait, at least 1 s), only where the planner can answer (a BKK stop it has not said it lacks, a FUTÁR station
  the planner knows or has not been asked about yet, or whose poles are
  known, a timetable trip, none of them failed lately, the planner not
  backing off) and a token of the boards' share is reserved for its
  answer before FUTÁR is asked (none for an answer the planner's caches
  hold: it asks the planner nothing): a dashboard's boards at once (40 poles)
  cut FUTÁR short for as many as the share holds, and the rest keep
  FUTÁR's whole budget, so a slow FUTÁR still answers them. The trip
  details reserved within 50 ms of each other share one token (up to one
  request's 40 trips; 20 at night, when a trip no board dated is asked for
  two days), as their one batched request spends one: a card's 40 trip
  details are all cut short on one token, and the rest of the share is
  left for the other cards' boards; a trip that lands in a later batch
  than the one that spent it takes a token when its request goes, if one
  is left. The token goes back when FUTÁR answers (a shared one once FUTÁR
  answered every trip on it), when every card that saw that request fail
  is done without spending it (a card asking the same again later does
  not hold it), and when every card that asked has left before FUTÁR
  failed, if that is within a second of its reservation; held longer, it
  counts as spent (the share refills meanwhile: 15 tokens given back after
  FUTÁR's 8 s let 18 requests go at once, past the burst of 15). Unloading the entry ends the trip-detail requests under way at
  once (their callers get no answer), and a fallback under way then asks
  the planner nothing. The planner then has 6 s. Elsewhere FUTÁR keeps
  the whole budget, and after it failed the planner is asked only if its
  answer still reaches the card (each card timed from its own ask, also
  one that joined a request under way), with half a second of slack.

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
  key. The integration asks it, while a card is shown, for the delays of
  the trips the card shows, at most once a minute per trip, and, only
  while BKK FUTÁR fails, for the departure boards of the Budapest stops
  the cards show (once per stop per 35 s) and their trips' details (once
  a minute per trip). All of it counts against one limit of one request
  every two seconds with a burst of 30 per Home Assistant, the BKK boards
  at most half of that. It keeps the answers in memory only, for those
  intervals, and republishes nothing. **Opting out:** Settings → Devices &
  services → Hungarian transport → Configure → *Ask mavplusz.hu (MÁV's
  journey planner)*, on by default. Off, Home Assistant sends nothing to
  mavplusz.hu: no delays, no vehicles, no BKK boards. The trains and buses
  then read only the `jbb.ghsq.de` feeds (as before 1.10.19), and a
  Budapest board or trip FUTÁR cannot answer gets FUTÁR's error, so the
  card fills it from Transitous. Saving the option reloads the entry; the
  next card poll follows it, and the one rate limit every planner request
  takes its token from reads the option too, so a request already on its
  way (a poll queued behind another card's) is not sent after it either. Removing the `"mavplusz": true` keys from
  `realtime_feeds.json` turns only the delays off; without a FUTÁR key no
  BKK board is asked of the planner either way.
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
  shares the copy. A copy no card has asked for in five of its intervals
  is dropped from memory (the static zips' train numbers are kept), also
  when the planner answers every call meanwhile or no card asks at all;
  the next ask fetches the feed anew.
- **Polite requests.** A descriptive User-Agent with the project's address;
  gzip; `If-None-Match` / `If-Modified-Since` where the server sends an ETag
  or Last-Modified (`gtfs.gpspositions.net` and the static zips do; the
  `jbb.ghsq.de` feeds do not); no redirects; a 20-second timeout; 8 MB per
  feed and 30 MB per static zip at most; at most 30 fetches in a burst and
  one every two seconds after that, whatever the cards ask.
- **The planner.** One request per action call at most for the trips not
  cached (each kept for the feed's interval), plus one for the route lists
  of route + start requests not cached (each kept three hours, at most
  2000 route lists, the least recently asked going first) and one for
  the vehicles of the live realtime-pattern trips' routes; JSON in,
  gzip, the same User-Agent, no redirects, a 20-second timeout, 1 MB per
  answer at most. While BKK FUTÁR fails, also the BKK boards (per stop per
  35 s) and trip details (see [BKK boards when FUTÁR
  fails](#bkk-boards-when-futár-fails)), with a User-Agent of their own.
  All planner requests of one Home Assistant share one rate limit, apart
  from the feeds' (asking the planner for many trips does not hold back
  the feeds' fetches) and as strict: 30 in a burst and one every two
  seconds after that, of which the BKK boards may use 15 and one every
  four seconds (a user asking for stops no card shows does not hold back
  the delays) and the vehicle requests of realtime-pattern trips 6 and one
  every ten seconds. A request one of these refuses spends no token of the
  others (every one is checked before any is taken from).
- **Outages.** A failing feed is not asked again for a minute (a static zip
  for 15 minutes), is logged once when it starts failing and once when it
  answers again, and the cards keep the timetable meanwhile (a static zip's
  train numbers from before stay in use, see above). A failing planner
  likewise: a minute's backoff, one warning, one line when it answers
  again, and the feeds meanwhile. The backoff is shared: the planner
  failing the BKK boards' requests (30 s; not a board's own failure, see
  [BKK boards when FUTÁR fails](#bkk-boards-when-futár-fails)) holds back
  the delays' requests too, and the other way round (not a delay
  request's own failure either: that holds back its own trips only). The vehicle requests
  alone back off on their own (3 minutes) and set no shared backoff.

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
day's midnight, up to 2880) or by `train` (digits). A `date` is eight
digits (YYYYMMDD); a row whose date is no calendar day (20261131) fails
no call and is never asked of the planner, not even for today: the feeds
match it as they can (a `train` row reads the feeds, a `trip_id` or
`route` + `start` row reads none). A `trip_id` or `route` that is only the
feed's prefix (`1:`) is never asked of the planner. Up to 40 trips of up to
120 stops per call; anything longer is refused before a feed is touched.
When nothing can be looked up the answer has no trips and an `error`:
`rt_no_feed` (no feed for that operator) or `rt_unavailable` (the feed cannot
be reached now).

## Privacy

The requests carry no personal data: the User-Agent names the project, not
the installation. A feed is asked for as a whole, not for the trips a card
shows. The planner is asked for what the cards show, and nothing else:
trip ids with their service dates (a row's own trip, the resolved trips of
a route + start row, and every trip of a train's number for today and
yesterday, so last night's runs too), route ids with a date for the route
lists of route + start rows, and route ids for the vehicles of live
realtime-pattern trips (and while BKK FUTÁR fails, the BKK ids below).
So mavplusz.hu can tell
which timetable rows some Home Assistant displays, as it can for anyone
using its own site. The vehicles' plate numbers in the feeds are not
passed on to the cards, and are not asked of the planner.

The planner fixtures are real answers to the module's own queries:
`tests/fixtures/realtime/mavplusz_trips.json` (2026-11-14, four Volán
trips, calls trimmed), `mavplusz_routes.json` (2026-11-14, the route
lists of Veszprém 2 and Miskolc 32 and 14 and two running trips),
`mavplusz_trains.json` (2026-11-18, four train runs, unedited),
`mavplusz_rt_pattern.json` (2026-11-18, three MODIFIED trains and their
routes' vehicles, unedited: one request of both kinds as the code asked
then, split in two by the tests) and `mavplusz_bkk.json` (2026-11-18, a
BKK board and trip details). Tests edit copies of them in memory only.
The other fixtures (`tests/fixtures/realtime/`) are real feeds of
2026-11-02 trimmed to a few trips, with the vehicle descriptors and entity
ids (plates) removed.

- **BKK boards while FUTÁR fails.** The planner is asked for the BKK stop,
  station and pole ids a card shows (and the poles FUTÁR named for a
  station) and the BKK trip ids of its rows with their service date,
  nothing else: mavplusz.hu can tell which Budapest stops and trips some
  Home Assistant displays while FUTÁR fails, as it can for anyone using
  its own site. Nothing is asked of it while FUTÁR answers.

## Decoding

Home Assistant does not ship the `protobuf` package: from 2024.10 to 2026.2
it is only a version pin in `package_constraints.txt`, not a dependency. The
integration reads the fields it needs with a small wire-format decoder in
`realtime.py` (FeedHeader timestamp; TripUpdate trip, stop_time_update
delays and times, schedule relationships; VehiclePosition trip, position,
timestamp, current stop; alerts are skipped), run in the
executor: the Volán intercity trip updates (1,584 trips, 48,000 stop updates)
take about 0.3 s on a laptop.

## Limits

- mavplusz.hu answers train numbers only through the static MÁV and
  GYSEV GTFS: while neither a copy of it nor its download is there, trains
  are matched on the feeds, which cannot match them either. The planner's
  vehicle names its trip but not the run's day: a daily train with
  realtime data on both last night's run and today's (a night train on its
  way while the next one already has predictions) shows the one vehicle on
  today's run.

- Cards with an index from before this change (no trip ids) get live data
  for Veszprém, Miskolc and MÁV only, until they get a newer index.
- The weekly index from GitHub Pages (or the release's) can be a few days
  older than the live feed: a trip the operator renamed since has no live
  data until the next weekly index (or the local daily timetable update).
- Debrecen and GYSEV as city operators depend on the static feed added
  elsewhere: mapped by the ids `debrecen` (menetbrand's slug; `dkv`, the
  Transitous name, is an alias) and `gysev`.
- A trip cancelled in the feed (`canceled`) or a stop it skips (`skipped`) is
  reported, but the card does not yet strike it through: it shows the
  timetable time there.
- The planner card does not use the feeds yet.
