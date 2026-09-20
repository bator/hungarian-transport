# Hungarian transport

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]

Home Assistant dashboard card for **BKK**, **MÁV**, **Volán** and other
Hungarian city operators (Miskolc, Pécs, Szeged, Szombathely, plus Volán
local networks such as Sopron or Eger).

Pick an origin and a destination, and the card lists the next departures between
those two stops. City and rail traffic in Budapest come from the live BKK FUTÁR
API. Long-distance coaches and local services outside Budapest come from official
GTFS feeds.

The editor and the card are available in **Hungarian and English**. By default
they follow the Home Assistant user's language; you can also pin one explicitly.

![Hungarian transport card](images/screenshot.png)

## Install

### HACS

1. HACS → Frontend → ⋮ → Custom repositories → add this repository with
   category **Dashboard**.
2. Download **Hungarian transport**.
3. HACS adds the Lovelace resource automatically. If you manage resources by
   hand, add:

   ```yaml
   url: /hacsfiles/hungarian-transport/hungarian-transport.js
   type: module
   ```

   `type: module` is required — the card resolves its own asset paths from the
   module URL.

### Manual

Copy `hungarian-transport.js` to `/config/www/hungarian-transport/` and add the
matching `/local/hungarian-transport/hungarian-transport.js` resource.

## Setup

Add the **Hungarian transport** card from the card picker (`custom:hungarian-transport-card`).
Older dashboards that still use `custom:bkk-stop-card-r3` keep working as an alias.

The editor asks for a BKK Open Data API key, which is free after a short sign-up:
<https://opendata.bkk.hu/data-sources>

The key is stored in the card configuration. If another Hungarian transport card
on the same dashboard already has one, the editor reuses it. When the key works,
the editor hides the field; it only reappears if the key is missing or FUTÁR
rejects it. Volán and Helyi modes do not need a key.

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
| `volanIndex` | string | next to the card | URL of `volan-index.json.gz` |
| `cityIndex` | string | next to the card | URL of `city-index.json.gz` |

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

Debrecen DKV does not publish a public GTFS zip, so it is not in the city
picker until a URL exists.

## Volán long-distance coaches

FUTÁR only covers coaches near Budapest, so long-distance Volán departures come
from the KTI GTFS feed, pre-built into a compact `volan-index.json.gz`.

Place that file next to the card JS. The card fetches it with `cache: 'no-store'`
and a daily cache-buster, so Home Assistant's 31-day `/local/` cache cannot pin
an old feed.

```bash
python3 scripts/build_volan_index.py
python3 scripts/build_city_index.py
```

`city-index.json.gz` sits next to the card JS as well. The daily job
(`scripts/update_volan_index.py`) HEADs the KTI zip **and** the municipal
feeds, rebuilds both indexes when something changed, and atomically replaces
the files.

To keep it current, `scripts/update_volan_index.py` HEADs the KTI zip, rebuilds
only when it changed, and atomically replaces the index. `packages/hungarian_transport.yaml`
runs it daily at 04:30 Europe/Budapest and 5 minutes after a core start.

Note that there is no direct coach between, for example, Székesfehérvár
autóbusz-állomás and Kelenföld — such services terminate at Népliget. The card
reflects the official GTFS, so it will not invent a connection.

## License

MIT — see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/bator/hungarian-transport
[release-url]: https://github.com/bator/hungarian-transport/releases
