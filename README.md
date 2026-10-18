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

For a frontend-only manual install, copy the files from `dist/` (including the
`leaflet/` folder, which the maps load next to the card) into
`/config/www/hungarian-transport/` and add the matching
`/local/hungarian-transport/hungarian-transport.js` resource (`type: module`).
The GitHub release attaches `hungarian-transport-frontend.zip` with these
frontend files. This does not install the ELVIRA backend.

Build the indexes first:

```bash
python3 scripts/build_volan_index.py
python3 scripts/build_city_index.py
```

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

- MÁV / GYSEV rail GTFS: <https://www.mavcsoport.hu/gtfs-igenybejelento>
  Put `mav-gtfs.zip` next to the scripts (or `MAV_GTFS_ZIP` / `MAV_GTFS_URL`
  in `.env`) and run `python3 scripts/build_mav_index.py`. Prefer unzipped
  **pfaedle** output (`MAV_PFAEDLE_DIR` or `/opt/pfaedle-trial/out`): official
  `shapes.txt` is a stub or an 8 km chord across Székesfehérvár–Kelenföld,
  while pfaedle follows OSM line 30. The card then draws every train's full
  nationwide line from `mav-index.json.gz`. Stubs that still miss the stop
  bbox become the stop sequence (IC 856 is Déli → Nagykanizsa). Without the
  zip the map still falls back to Transitous / FUTÁR.
- National Access Point (SZKT official dump and others): <https://napportal.kozut.hu/>
- Kecskemét KEKO city buses: <https://timetable.keko.hu/Kecskemet_GTFS.zip> (auth)

BKK static GTFS is public, but Budapest already uses live FUTÁR, so that zip
is not folded into `city-index.json.gz`. The BKK Open Data key is only for
FUTÁR / GTFS-RT.

## Keeping the GTFS indexes current

HACS installs the indexes that were built when the GitHub release was cut.
A weekly GitHub Action rebuilds the indexes from the latest stable release's
code and re-uploads that release's assets, so fresh downloads get current
feeds. HACS does not re-download a version that is already installed, so
existing installs only pick up new feeds with the next release, or with the
local rebuild below.

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
   release ships a newer index schema than your copied scripts build (larger
   local `mav-index.json.gz` wins). A local `mav-gtfs.zip` or pfaedle directory also rebuilds
   `mav-index.json.gz`. A MÁV rebuild failure notifies and exits 1, like Volán
   and city. The shapes folders travel with their indexes. Simplifying the
   Volán `shapes.txt` (8 million points) takes about a minute on a desktop and
   longer on a Raspberry Pi; `run_update.sh` allows 30 minutes.

Set `HUNGARIAN_TRANSPORT_WWW` if you installed the card somewhere else, for
example `/config/www/hungarian-transport` for the manual `/local` install.
Point `notify.admins` at whatever notifier you use, or drop that automation.

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

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/bator/hungarian-transport
[release-url]: https://github.com/bator/hungarian-transport/releases
