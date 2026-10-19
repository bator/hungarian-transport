# Hungarian transport

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]

Home Assistant dashboard card for **BKK**, **MÁV**, **Volán** and Hungarian
city operators (Miskolc, Pécs, Szeged, Szombathely, Veszprém, Paks, Dombóvár,
Kecskeméti Kisvasút, Budaörs, Bahart, Hódmezővásárhely, Kaposvár, Kiskunhalas,
Székesfehérvár, Tatabánya, Várpalota, plus Volán local networks such as
Sopron or Eger).

Pick an origin and a destination. The card lists the next departures between
those two stops. City and rail traffic in Budapest come from the live BKK FUTÁR
API. Long-distance coaches and local services outside Budapest come from
official GTFS feeds, shipped next to the card as compact gzip indexes.

![Hungarian transport card listing S40 trains and a Volán coach from Kelenföld to Székesfehérvár](docs/card.svg)

The editor and the card are available in **Hungarian and English**. By default
they follow the Home Assistant user's language; you can pin one explicitly.

## Install

### HACS

This release is a HACS **Integration** so it installs both the ELVIRA backend
and the card. Existing users must make this one-time migration:

1. In HACS, remove the existing `hungarian-transport` custom repository that
   was added as **Dashboard**.
2. Add `https://github.com/bator/hungarian-transport` again as a custom
   repository with category **Integration**, then download a GitHub release.
3. Restart Home Assistant, then go to **Settings → Devices & services → Add
   integration** and add **Hungarian transport** once.

The integration registers `hungarian_transport.elvira_between` and loads the
card module and its GTFS indexes automatically; no Lovelace resource entry or
separate ELVIRA helper is needed. Future releases update the integration and
card together through HACS.

### Manual

For a frontend-only manual install, unzip `hungarian-transport-frontend.zip`
from a GitHub release into `/config/www/hungarian-transport/` and add the
`/local/hungarian-transport/hungarian-transport.js` resource (`type: module`).
The zip holds the card bundle, the `leaflet/` folder the maps load, and the
GTFS indexes with their shapes folders. This does not install the ELVIRA
backend.

To build the indexes yourself, run the builders from the repository root.
They write into the current directory, not into `dist/`:

```bash
python3 scripts/build_volan_index.py   # volan-index.json.gz, volan-shapes-<hash>/
python3 scripts/build_city_index.py    # city-index.json.gz, city-shapes-<hash>/
python3 scripts/audit_city_shapes.py city-index.json.gz   # does each line reach its stops, in order?
```

Then copy `dist/hungarian-transport.js`, `dist/leaflet/`, both indexes and the
shapes folder each index names into `/config/www/hungarian-transport/`. The
`*.json.gz` files committed in `dist/` are an old snapshot without shapes; do
not ship them. For the daily rebuild below, set
`HUNGARIAN_TRANSPORT_WWW=/config/www/hungarian-transport`.

## Setup

Add the **Hungarian transport** card from the card picker
(`custom:hungarian-transport-card`). Older dashboards that still use
`custom:bkk-stop-card-r3` keep working as an alias.

The planner companion (`custom:hungarian-transit-stop-card-plan`) is in the
same picker and has its own visual editor. It always searches every operator
(BKK, Volán, MÁV); there are no mode toggles. Stop search and transfers go
through [Transitous](https://transitous.org/) first; BKK FUTÁR is the fallback
when Transitous has no hit. The older `custom:bkk-stop-card-plan` type remains
an alias.

BKK and MÁV modes need a BKK Open Data API key, free after a short sign-up:
<https://opendata.bkk.hu/data-sources>

The recommended place for the key is the integration: enter it when adding
**Hungarian transport**, or later under **Settings → Devices & services →
Hungarian transport → Configure**. Cards without their own key then reach BKK
FUTÁR through the `hungarian_transport.futar` service, so the key never
appears in dashboard configs or in browser requests, and identical requests
from several cards and devices share one call. The editor shows that the key
comes from the integration.

A key can still be set per card (`apiKey`), which takes precedence. If another
Hungarian transport card on the same dashboard already has one and the
integration has none, the editor reuses it. When the key works, the editor
hides the field and shows a **Change** link; the field reappears if the key is
missing or FUTÁR rejects it. Volán and Helyi modes
do not need a key.

Live positions come from BKK FUTÁR for Budapest city and rail, from MÁV EMMA
for trains, from utas.hu for Volán coaches, and from Szombathely GTFS-RT for
that city's buses. Other municipal operators stay on the official static GTFS.
Train, coach, and Szombathely dots need the companion `bkk_stop` services
`coach_positions` and `coach_shape` on the Home Assistant host. The MÁV
seat-reservation marker is provided by this integration's
`hungarian_transport.elvira_between` service. The timetable still lists
without the optional position services.

Tapping a Volán or city departure opens a map with the trip's route from the
feed's own `shapes.txt`. The shapes are simplified (10 m) and shipped next to
the indexes in `volan-shapes-<hash>/` and `city-shapes-<hash>/`, split into 64
small files, so opening a map downloads one file of at most ~140 kB and no
third-party service is involved. A trip without a shape falls back to
Transitous, and then to a line through its stops. City trams, trolleybuses and
the Bahart ferry keep their GTFS vehicle type.

Every map marks where you get on and off, with the stop's name:

| Pin | Meaning |
|---|---|
| Dot in the line's colour, with the line's badge | Boarding |
| White ring in the line's colour | Alighting, when the card has a destination |
| White ring with a dark border | Transfer at the same stop |
| Blue dot, dark target | Start and end of a planner itinerary that begins or ends with a walk |

The alighting stop is the trip's own stop when the GTFS feed or BKK FUTÁR
lists it. Otherwise it comes from Transitous or the configured destination.

### MÁV train routes

MÁV publishes its timetable (GTFS, CC0 licence) only to registered users, so
the release cannot ship it. Without it a train's map relies on a live
Transitous lookup, which sometimes finds no trip, and on BKK FUTÁR, which
only knows trains around Budapest. With the feed, every train in Hungary
draws its own route.

1. Register at <https://www.mavcsoport.hu/gtfs-igenybejelento>. MÁV emails
   the download address and a login.
2. Download the zip (`gtfsMavMenetrend.zip`).
3. Put it at `/config/hungarian-transport/mav-gtfs.zip`. With the
   **File editor** app: open the `hungarian-transport` folder (create it if
   missing), choose *Upload file*, then rename the file to `mav-gtfs.zip`.
4. Restart Home Assistant, or run the `hungarian_transport.rebuild_mav_index`
   action.

The integration then builds `mav-index.json.gz` next to the card:

- **Every stop pattern of every train.** A train number can run a longer or
  shorter route on different days (936 sometimes continues to Szombathely),
  so each pattern is kept with its route and its stations. The map picks the
  pattern that serves your two stations, in order, and puts the pins on that
  train's own station coordinates.
- **Checked trip by trip.** After each build, every trip in the feed is
  drawn the way the card would draw it, and every one of its stations must
  lie within 300 m of the route. If fewer than 99% of the trains pass, a
  notification says so. The result is in the action's response and in the
  log.
- **Rebuilt when needed.** A HACS update replaces the card's folder, so the
  index is rebuilt at startup whenever it is missing or older than the zip.

MÁV's own `shapes.txt` is coarse: an 8 km chord cuts across the fields
between Székesfehérvár and Kelenföld. A route that misses one of its
stations is replaced by the line through its stations, so a train never
skips its stops on the map. For routes that follow the tracks, run the feed
through [pfaedle](https://github.com/ad-freiburg/pfaedle) first:

```bash
docker run --rm -v "$PWD":/data ghcr.io/ad-freiburg/pfaedle:latest \
  -D -m rail -x /data/hungary-latest.osm.pbf -i /data/mav-gtfs.zip
```

Then zip `gtfs-out/` and use that as `mav-gtfs.zip`. With a pfaedle feed
(October 2026) all 24,994 trips of 2,623 trains passed the check.

**Updating automatically.** An admin can upload the zip instead of copying
it. The response contains the build and check results:

```bash
curl -X POST -H "Authorization: Bearer $HA_TOKEN" \
  --data-binary @mav-gtfs.zip \
  http://homeassistant.local:8123/api/hungarian_transport/mav_gtfs
```

A daily job that downloads (and pfaedle-matches) the feed can end with this
call, so the card follows timetable changes on its own.

## Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `type` | string | — | `custom:hungarian-transport-card` |
| `apiKey` | string | — | BKK Open Data API key. Optional when the integration holds the key. |
| `language` | string | `auto` | `auto`, `hu` or `en`. `auto` follows the Home Assistant user's language. |
| `name` | string | `origin → destination` | Card header |
| `stopId` | string | — | Origin stop, set by the editor |
| `stopName` | string | — | Origin stop name |
| `destKey` | string | — | Destination key, set by the editor |
| `destName` | string | — | Destination stop name |
| `routeIds` | list | `[]` | Routes serving the destination, filled by the editor |
| `mav` | bool | `false` | MÁV mode: rail stations and trains only |
| `volan` | bool | `false` | Volán mode: long-distance coaches |
| `helyi` | bool | `false` | Local-city mode (mutually exclusive with MÁV/Volán) |
| `city` | string | — | Operator id from `city-index.json.gz` (`pecs`, `miskolc`, `sopron`, …) |
| `refresh` | number | `45` | Seconds between departure refreshes, minimum 15 |
| `minutesAfter` | number | `180` | How many minutes ahead to list departures (15–480). The editor offers presets 30–480; YAML may use any value in range. Also applies to the planner card. |
| `volanIndex` | string | next to the card | URL of `volan-index.json.gz` |
| `cityIndex` | string | next to the card | URL of `city-index.json.gz` |
| `mavIndex` | string | next to the card | URL of `mav-index.json.gz` (nationwide train lines) |
| `favorites` | list | national hubs | Optional `{ id, name }` chips in the editor |

### Example

```yaml
type: custom:hungarian-transport-card
language: en
mav: true
stopId: BKK_005501024
stopName: Budapest-Kelenföld
destKey: székesfehérvár
destName: Székesfehérvár
minutesAfter: 180
```

## Modes

| Toggle | Source | Search scope |
| --- | --- | --- |
| (none) | BKK FUTÁR | BKK stops |
| MÁV | BKK FUTÁR rail | MÁV stations |
| Volán | FUTÁR local coaches + KTI GTFS | Volán long-distance stops |
| Helyi | municipal GTFS + Volán local | stops in the chosen city |

Destination search only offers stops that a vehicle from the origin actually
reaches.

The companion planner card (`custom:hungarian-transit-stop-card-plan`)
picks an origin and a destination, then lists every service between them
(BKK, Volán and MÁV) in the same timetable layout as the hop card. Search and
transfers use Transitous first and fall back to BKK FUTÁR. Add it from the
card picker; the visual editor and the on-card pickers both set look-ahead
(`minutesAfter`) and can swap origin and destination. A map overlay draws the
full itinerary. The older `custom:bkk-stop-card-plan` type remains registered
as an alias.

The suggestions under the origin are your own stops: every origin and
destination you pick counts, the most used ones (and the recent ones, as an old
count halves every 30 days) come first, and the one already picked is left out.
They are kept per Home Assistant user in that user's own frontend storage, so
your phone and your tablet show the same list; a new user sees none until
they pick a stop.

Under **When** the planner switches between **Now** (the live departure
board, plus a faster transfer itinerary when there is one), **Depart** and
**Arrive**. With a time picked it lists up to six itineraries for that moment,
leaving at or after it or arriving by it. Itineraries that another one beats
on both departure and arrival are dropped. Each itinerary shows its times,
duration, transfers and the lines as coloured badges; the open one adds a map
and a timeline of every leg. The time stays on the card and is not saved in
the dashboard configuration.

Debrecen DKV is marked not public on the [menetbrand catalog](https://gtfs.menetbrand.com/letoltes/),
so it stays out of the city picker. Weekendbus (Csömör/Pécel) still hosts a
zip, but the calendar ended 2025-12-31, so it is not wired. Those lines now
run as Volán / MÁV.

Where the publisher has no fixed zip URL, the city index uses the public
menetbrand `/download/<slug>` proxy (Kaposvár, Hódmezővásárhely,
Kiskunhalas, Székesfehérvár, Tatabánya, Várpalota). Bahart prefers the
official keyed NAP URL from `BAHART_GTFS_URL` in a gitignored `.env`
(`/config/hungarian-transport/.env` on Home Assistant). Transitland v2 and
Mobility Database keys in the same file are a catalog fallback. Without
those secrets the public menetbrand zip is used, including GitHub Actions
unless the matching repository secrets are set.

These feeds still need a form or login:

- MÁV / GYSEV rail GTFS: <https://www.mavcsoport.hu/gtfs-igenybejelento>.
  See [MÁV train routes](#máv-train-routes) for how to add it.
- National Access Point (SZKT official dump and others): <https://napportal.kozut.hu/>
- Kecskemét KEKO city buses: <https://timetable.keko.hu/Kecskemet_GTFS.zip> (auth)

BKK static GTFS is public, but Budapest already uses live FUTÁR, so that zip
is not folded into `city-index.json.gz`. The BKK Open Data key is only for
FUTÁR / GTFS-RT.

## Keeping the GTFS indexes current

HACS installs the indexes that were built when the GitHub release was cut.
A weekly GitHub Action rebuilds the indexes from the latest stable release's
code and re-uploads that release's assets, so fresh downloads get current
feeds. A release run compares the new indexes with the latest published
ones: a city feed that is gone, a feed that lost more than half of its trips,
or a Volán index with far too few trips fails the run, and the published
assets stay as they are (see [When an update fails](#when-an-update-fails)).
To accept a feed that is gone for good, list its id in the `allow_missing`
input of a manual run, or in the `HUNGARIAN_TRANSPORT_ALLOW_MISSING`
repository variable for every run including the weekly one (the input adds
to the variable), and drop it from `CITY_FEEDS`.
HACS does not re-download a version that is already installed, so existing
installs only pick up new feeds with the next release, or with the local
rebuild below.

To rebuild them yourself on Home Assistant OS:

1. Copy `scripts/*.py` and `scripts/run_update.sh` to `/config/hungarian-transport/`
   (`build_mav_index.py` included). pfaedle output stays on the builder host,
   not on Home Assistant OS — without it the official zip is stop-sequence,
   not OSM rails.
2. Copy `packages/hungarian_transport.yaml` into a Home Assistant package
   directory (`packages: !include_dir_named packages`).
3. Restart Home Assistant. The automation runs at 04:30 Europe/Budapest and
   five minutes after a core start. It HEADs the KTI zip and the municipal
   feeds, rebuilds only when the fingerprint changed, and writes
   `volan-index.json.gz` / `city-index.json.gz` atomically into
   `/config/custom_components/hungarian_transport/www/`, where the integration
   serves the card from (the old `/config/www/community/hungarian-transport/`
   folder is used only when the integration is not installed). A durable copy
   is also kept under `/config/hungarian-transport/` so a HACS update that
   overwrote the integration folder is restored on the next run, unless the
   release ships a newer index schema than your copied scripts build. For
   `mav-index.json.gz`, of two builds of the same feed the larger
   (pfaedle-based) one wins, otherwise the later build, so an index the
   integration built from an uploaded zip is kept. A local `mav-gtfs.zip` or
   pfaedle directory also rebuilds `mav-index.json.gz`; with `MAV_GTFS_URL`
   set, each daily run downloads the zip again once it is 20 hours old. A MÁV
   index that fails its audit is not written. Volán, city and MÁV fail
   independently: each failure notifies and keeps that index as it was, and
   the rest of the run goes on. A city feed that failed is downloaded and
   rebuilt again on the next run; one left out because its calendar ended is
   not, until the publisher posts a new zip. The shapes folders travel with
   their indexes. Simplifying the Volán `shapes.txt` (8 million points) takes
   about a minute on a desktop and longer on a Raspberry Pi; `run_update.sh`
   allows 30 minutes.

Set `HUNGARIAN_TRANSPORT_WWW` if you installed the card somewhere else, for
example `/config/www/hungarian-transport` for the manual `/local` install.
Point `notify.admins` at whatever notifier you use, or drop that automation.

### When an update fails

The local update sends its failures to the webhook (and so to
`notify.admins`); `run_update.sh` sends the ones the script cannot report
itself. `/config/hungarian-transport/update.log` has the details.

- *… frissítés sikertelen: …* — a feed did not download, did not parse or
  failed a guard. That index stays as it was and the next run tries again.
- *… el sem indult* or *… összeomlott (exit N)* — the script could not start
  or crashed. Usually `/config/hungarian-transport/` holds a half-copied set
  of scripts: copy all of `scripts/` again.
- *… megszakítva* or *… megölve* — Home Assistant restarted during the run,
  the 30-minute limit hit, or the host ran out of memory.

The city index (locally and in a release run) is not replaced when the
rebuild would lose a feed that the index in place still runs, or more than
half of a feed's trips. The message names the feed:

- `<id> missing (had N trips)` — the feed did not download or parse.
- `<id> calendar ended YYYY-MM-DD (had N trips, the previous index runs until
  YYYY-MM-DD)` — the publisher's new zip ends before the timetable already
  in the index.
- `<id> N trips vs M` — most of the feed's trips are gone.

A feed whose calendar has ended is left out of the index with a warning.
That is not a failure once the timetable the index had for it has ended as
well: Bahart after the last sailing of the season, the Kecskemét narrow-gauge
railway out of season, or a feed published for a few weeks at a time
(Miskolc) whose next zip is late. It comes back with the publisher's next
zip. A Volán index whose calendar has ended is refused outright.

To accept a loss on purpose, set `HUNGARIAN_TRANSPORT_ALLOW_MISSING` to the
feed ids (comma-separated, `volan-local` for the Volán local lines, or
`all`) in `/config/hungarian-transport/.env`, and clear it once the feed is
back; on GitHub use the `allow_missing` input or the repository variable
above.

There is no direct coach between, for example, Székesfehérvár
autóbusz-állomás and Kelenföld — such services terminate at Népliget. The card
reflects the official GTFS, so it will not invent a connection.

## Development

The card sources are ES modules in `src/` (`lib/` holds the FUTÁR, Transitous,
GTFS and map helpers, one file per area). `dist/hungarian-transport.js` is the
esbuild bundle that HACS ships; do not edit it by hand. The integration hands
Home Assistant `dist/hungarian-transport-loader.js` (built from
`src/loader.js`), which imports the bundle and retries a failed download, so a
dropped connection does not leave the cards on "Configuration error".

```bash
npm ci
npm run lint
npm run build
npm test
```

CI fails when the committed bundle or loader does not match `npm run build`.

The GTFS scripts in `scripts/` are standard-library Python. Their tests need
no network (the updater test also runs `run_update.sh`, so bash and
coreutils `timeout`), and CI lints the Python with ruff:

```bash
python3 scripts/test_gtfs_compact.py
python3 scripts/test_update_volan_index.py
python3 scripts/test_build_guards.py
pip install ruff==0.16.10
ruff check --target-version py312 --select E,F,W,B --ignore E501,B905 custom_components scripts
```

`python3 scripts/audit_city_shapes.py city-index.json.gz` reports, per
operator, how every stop pattern lines up with its `shapes.txt` route; the
release run puts the report on its summary.

Integration tests need Python 3.13:

```bash
pip install -r requirements_test.txt
python -m pytest
```

## Credits

- [BKK Open Data / FUTÁR](https://opendata.bkk.hu/) for Budapest city and rail
  realtime.
- [Transitous](https://transitous.org/) for nationwide stop search and
  transfer planning.
- [KTI](https://gtfs.kti.hu/) for the national Volán GTFS.
- Municipal GTFS: MVK (Miskolc), Tüke Busz (Pécs), SZKT (Szeged), Blaguss
  (Szombathely), V-Busz (Veszprém), Paksbusz (Paks), PTKE (Dombóvár),
  Kecskeméti Kisvasút, HOMM (Budaörs), Bahart, HVSZ (Hódmezővásárhely),
  KKZRT (Kaposvár), Halasbusz (Kiskunhalas), Székesfehérvár helyi, T-Busz
  (Tatabánya), THURY-BUSZ (Várpalota).
- [MÁV-csoport](https://www.mavcsoport.hu/gtfs-igenybejelento) for the rail
  GTFS you add yourself (CC0).
- [pfaedle](https://github.com/ad-freiburg/pfaedle) (University of Freiburg)
  for matching train routes to the rails, on
  [OpenStreetMap](https://www.openstreetmap.org/copyright) data
  (© OpenStreetMap contributors, ODbL).

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/bator/hungarian-transport
[release-url]: https://github.com/bator/hungarian-transport/releases
