# Hungarian transport

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]

Home Assistant dashboard card for **BKK**, **MÁV**, **Volán** and Hungarian
city operators (Miskolc, Pécs, Szeged, Szombathely, plus Volán local networks
such as Sopron or Eger).

Pick an origin and a destination. The card lists the next departures between
those two stops. City and rail traffic in Budapest come from the live BKK FUTÁR
API. Long-distance coaches and local services outside Budapest come from
official GTFS feeds, shipped next to the card as compact gzip indexes.

![Hungarian transport card listing S40 trains and a Volán coach from Kelenföld to Székesfehérvár](docs/card.svg)

The editor and the card are available in **Hungarian and English**. By default
they follow the Home Assistant user's language; you can pin one explicitly.

## Install

### HACS

1. HACS → Frontend → ⋮ → Custom repositories → add
   `https://github.com/bator/hungarian-transport` with category **Dashboard**.
2. Download **Hungarian transport**.
3. HACS adds the Lovelace resource automatically. If you manage resources by
   hand, add:

   ```yaml
   url: /hacsfiles/hungarian-transport/hungarian-transport.js
   type: module
   ```

   `type: module` is required — the card resolves `volan-index.json.gz` and
   `city-index.json.gz` from the module URL.

A HACS download is a GitHub **release zip** (`hungarian-transport.zip`). It
contains the card **and** both GTFS indexes. The default branch is not a
working install by itself.

### Manual

Copy `hungarian-transport.js`, `volan-index.json.gz` and `city-index.json.gz`
into `/config/www/hungarian-transport/` and add the matching
`/local/hungarian-transport/hungarian-transport.js` resource (`type: module`).

Build the indexes first:

```bash
python3 scripts/build_volan_index.py
python3 scripts/build_city_index.py
```

## Setup

Add the **Hungarian transport** card from the card picker
(`custom:hungarian-transport-card`). Older dashboards that still use
`custom:bkk-stop-card-r3` keep working as an alias.

BKK and MÁV modes need a BKK Open Data API key, free after a short sign-up:
<https://opendata.bkk.hu/data-sources>

The key is stored in the card configuration. If another Hungarian transport
card on the same dashboard already has one, the editor reuses it. When the
key works, the editor hides the field and shows a **Change** link; the field
reappears if the key is missing or FUTÁR rejects it. Volán and Helyi modes
do not need a key.

Live vehicle positions and delays come from BKK FUTÁR (BKK and MÁV modes).
No other Hungarian operator publishes a public, key-based realtime API, so
Volán long-distance and Helyi city services stay on the official static GTFS.

## Options

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `type` | string | — | `custom:hungarian-transport-card` |
| `apiKey` | string | — | BKK Open Data API key |
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
| `minutesAfter` | number | `180` | How many minutes ahead to list departures (15–360). The editor offers presets 30–360; YAML may use any value in range. Also applies to the planner card. |
| `volanIndex` | string | next to the card | URL of `volan-index.json.gz` |
| `cityIndex` | string | next to the card | URL of `city-index.json.gz` |
| `favorites` | list | national hubs | Optional `{ id, name }` chips in the editor |

### Example

```yaml
type: custom:hungarian-transport-card
apiKey: 00000000-0000-0000-0000-000000000000
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
(BKK, Volán and MÁV) in the same timetable layout as the hop card. The older
`custom:bkk-stop-card-plan` type remains registered as an alias.

Debrecen DKV does not publish a public GTFS zip, so it is not in the city
picker until a URL exists.

## Keeping the GTFS indexes current

HACS installs the indexes that were built when the GitHub release was cut.
A weekly GitHub Action publishes a patch release when the feeds move.

To rebuild them yourself on Home Assistant OS:

1. Copy `scripts/*.py` and `scripts/run_update.sh` to `/config/hungarian-transport/`.
2. Copy `packages/hungarian_transport.yaml` into a Home Assistant package
   directory (`packages: !include_dir_named packages`).
3. Restart Home Assistant. The automation runs at 04:30 Europe/Budapest and
   five minutes after a core start. It HEADs the KTI zip and the municipal
   feeds, rebuilds only when the fingerprint changed, and writes
   `volan-index.json.gz` / `city-index.json.gz` atomically into
   `/config/www/community/hungarian-transport/`.

Set `HUNGARIAN_TRANSPORT_WWW` if you installed the card somewhere else.
Point `notify.admins` at whatever notifier you use, or drop that automation.

There is no direct coach between, for example, Székesfehérvár
autóbusz-állomás and Kelenföld — such services terminate at Népliget. The card
reflects the official GTFS, so it will not invent a connection.

## Credits

- [BKK Open Data / FUTÁR](https://opendata.bkk.hu/) for Budapest city and rail
  realtime.
- [KTI](https://gtfs.kti.hu/) for the national Volán GTFS.
- Municipal GTFS: MVK (Miskolc), Tüke Busz (Pécs), SZKT (Szeged), Blaguss
  (Szombathely).

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/bator/hungarian-transport
[release-url]: https://github.com/bator/hungarian-transport/releases
