# Hungarian transport

[![hacs][hacs-badge]][hacs-url]
[![release][release-badge]][release-url]

Home Assistant integration and dashboard cards for Hungarian public
transport: **BKK**, **MÁV**, **Volán** and 16 city networks. See the next
departures between two stops, follow a vehicle on the map, and plan a trip
across every network. In Hungarian and English.

| Departures | Route map |
| --- | --- |
| ![MÁV trains from Budapest-Kelenföld to Székesfehérvár, with platforms and seat-reservation marks][img-card-mav] | ![Tram 47 on its route, with its live position and the boarding and alighting stops][img-map-bkk] |

| Planner | Card editor |
| --- | --- |
| ![The planner with a transfer itinerary from Budapest-Keleti to Szeged][img-planner] | ![The card editor with its live preview][img-editor] |

📖 **[User manual][manual-en]** · **[Felhasználói kézikönyv][manual-hu]**:
every screen, step by step, with screenshots.

## Features

- **Departure card:** the next departures between two stops, with live
  delays, platforms, seat-reservation and national-pass marks, accessibility
  and bike icons, and the travel time to your stop.
- **Four modes:** Budapest (BKK FUTÁR, live), MÁV trains, Volán coaches, and
  city networks (Miskolc, Pécs, Szeged, Szombathely, Veszprém, Kecskemét
  narrow-gauge railway, Balaton ferries and more).
- **Route map:** the trip's own route, where you get on and off, and the
  vehicle's live or estimated position.
- **Planner:** search any stop, street or town; live board, or itineraries
  with transfers for a time you pick, with a map and a timeline. It suggests
  the stops you use most.
- **Integration:** keeps your BKK key out of dashboards, and adds MÁV
  platforms and seat reservations (ELVIRA).

## Install

1. Open the repository in HACS and click **Download**:

   [![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bator&repository=hungarian-transport&category=integration)

2. Restart Home Assistant.
3. Add the integration, and enter your free
   [BKK Open Data key](https://opendata.bkk.hu/data-sources) there (BKK and
   MÁV cards need it; Volán and city cards do not):

   [![Open your Home Assistant instance and start setting up Hungarian transport.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hungarian_transport)

4. Add the **Hungarian transport** or the **Hungarian transit stop planner**
   card to a dashboard, and pick two stops in its editor.

Without the buttons: in HACS add `https://github.com/bator/hungarian-transport`
as a custom repository with category **Integration**, and after the restart
go to **Settings → Devices & services → Add integration → Hungarian
transport**.

Installed it earlier as a HACS **Dashboard** repository? Remove that
repository first, then add it again as an **Integration**.

| Budapest (BKK) | Volán | City network |
| --- | --- | --- |
| ![Metro M4 and trams from Móricz Zsigmond körtér to Fővám tér][img-bkk] | ![Coaches from Népliget to Kecskemét][img-volan] | ![Szeged tram 2][img-city] |

## Configuration

The editors set everything. In YAML:

```yaml
type: custom:hungarian-transport-card
mav: true                  # or volan: true, or helyi: true with city: szeged
stopId: BKK_005501024
stopName: Budapest-Kelenföld
destKey: szekesfehervar
destName: Székesfehérvár
minutesAfter: 180          # look-ahead, 15–480 minutes
```

```yaml
type: custom:hungarian-transit-stop-card-plan
```

Every option: [configuration reference][manual-config]. A key on the card
(`apiKey`) still works but is deprecated: every Home Assistant user can read
it from the dashboard. Keep it in the integration.

## Data

| What | Source |
| --- | --- |
| Budapest buses, trams, metro, HÉV, suburban trains | [BKK FUTÁR](https://opendata.bkk.hu/), live |
| MÁV trains | BKK FUTÁR, MÁV ELVIRA (platforms, seat reservations) |
| Volán coaches | [KTI](https://gtfs.kti.hu/) national GTFS, shipped with each release |
| City networks | Each operator's GTFS, shipped with each release |
| Stop search, transfers | [Transitous](https://transitous.org/), BKK FUTÁR as fallback |
| Train routes on the map (optional) | [MÁV GTFS][timetables-mav] you add yourself |

A weekly job publishes a new patch release when a timetable changed; a
published release never changes. To refresh the timetables daily on your own
Home Assistant, add the MÁV feed, or read what an update message means, see
[Timetables, train routes and updates][timetables].

## Development

Card sources are ES modules in `src/`; `dist/` is the esbuild bundle (CI
checks it matches `npm run build`). The GTFS scripts in `scripts/` are
standard-library Python.

```bash
npm ci && npm run lint && npm run build && npm test
for t in scripts/test_*.py; do python3 "$t"; done
pip install -r requirements_test.txt && python -m pytest   # Python 3.13
```

## Credits

Timetables and live data: [BKK Open Data](https://opendata.bkk.hu/),
[KTI](https://gtfs.kti.hu/), [MÁV-csoport](https://www.mavcsoport.hu/gtfs-igenybejelento)
(CC0), [Transitous](https://transitous.org/), and the city operators MVK,
Tüke Busz, SZKT, Blaguss, V-Busz, Paksbusz, PTKE, Kecskeméti Kisvasút, HOMM,
Bahart, HVSZ, KKZRT, Halasbusz, Székesfehérvár helyi, T-Busz and THURY-BUSZ.
Train routes matched to the rails with
[pfaedle](https://github.com/ad-freiburg/pfaedle) on
[OpenStreetMap](https://www.openstreetmap.org/copyright) data
(© OpenStreetMap contributors, ODbL).

## License

MIT, see [LICENSE](LICENSE).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
[release-badge]: https://img.shields.io/github/v/release/bator/hungarian-transport
[release-url]: https://github.com/bator/hungarian-transport/releases
[manual-en]: https://github.com/bator/hungarian-transport/blob/main/docs/manual/README.md
[manual-hu]: https://github.com/bator/hungarian-transport/blob/main/docs/manual/README.hu.md
[manual-config]: https://github.com/bator/hungarian-transport/blob/main/docs/manual/README.md#11-configuration-reference
[timetables]: https://github.com/bator/hungarian-transport/blob/main/docs/timetables.md
[timetables-mav]: https://github.com/bator/hungarian-transport/blob/main/docs/timetables.md#máv-train-routes
[img-card-mav]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/card-mav.webp
[img-map-bkk]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/map-bkk.webp
[img-planner]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/planner-transfer.webp
[img-editor]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/editor-mav-done.webp
[img-bkk]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/card-bkk.webp
[img-volan]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/card-volan.webp
[img-city]: https://raw.githubusercontent.com/bator/hungarian-transport/main/docs/manual/images/en/card-city.webp
