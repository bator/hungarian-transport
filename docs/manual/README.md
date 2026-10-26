# Hungarian transport: user manual

**English** · [Magyar](README.hu.md)

This manual walks through every screen of the Hungarian transport
integration and its two dashboard cards: the **departure card**, which lists
the next departures between two stops, and the **planner**, which finds a
route between any two places in Hungary.

All screenshots were taken from the real cards with live data on a weekday
morning in October 2026. Times, delays and vehicles in your dashboard will
of course differ.

## Contents

1. [What you get](#1-what-you-get)
2. [Installation](#2-installation)
3. [The integration](#3-the-integration)
4. [The departure card](#4-the-departure-card)
5. [Reading a departure row](#5-reading-a-departure-row)
6. [The route map](#6-the-route-map)
7. [The planner](#7-the-planner)
8. [Layout, language and themes](#8-layout-language-and-themes)
9. [Keeping timetables current](#9-keeping-timetables-current)
10. [Troubleshooting](#10-troubleshooting)
11. [Configuration reference](#11-configuration-reference)

---

## 1. What you get

| Part | What it does |
| --- | --- |
| **Hungarian transport integration** | Loads the cards into Home Assistant, keeps the BKK key out of your dashboards, and provides the MÁV timetable service (platforms, seat reservations). |
| **Departure card** (`custom:hungarian-transport-card`) | The next departures between an origin and a destination: BKK, MÁV, Volán coaches or a city's local network. |
| **Planner** (`custom:hungarian-transit-stop-card-plan`) | Search any stop, street or town in Hungary, then see the live board between them, or itineraries with transfers for a chosen time. |

Where the data comes from:

| Service | Source | Live? |
| --- | --- | --- |
| Budapest buses, trams, trolleybuses, metro, HÉV, suburban trains | BKK FUTÁR | Yes, real-time positions and delays |
| MÁV trains | BKK FUTÁR around Budapest, MÁV ELVIRA for platforms and seat reservations | Delays and platforms |
| Volán coaches | National Volán GTFS (opendata.utas.hu, KTI as fallback), shipped with the card | Timetable |
| City networks (Szeged, Pécs, Miskolc, …) | Each operator's GTFS, shipped with the card | Timetable (Szombathely real-time with the companion services) |
| Stop search and transfers | [Transitous](https://transitous.org/), BKK FUTÁR as fallback | — |

---

## 2. Installation

### 2.1 With HACS

The quickest way is two buttons. Each opens your own Home Assistant (the
first time, my.home-assistant.io asks for its address).

1. Open the repository in HACS, then click **Download**:

   [![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bator&repository=hungarian-transport&category=integration)

2. Restart Home Assistant.
3. Start setting up the integration:

   [![Open your Home Assistant instance and start setting up Hungarian transport.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=hungarian_transport)

Without the buttons:

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/bator/hungarian-transport` with the category
   **Integration**.
3. Find **Hungarian transport** in HACS and download it.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration** and pick
   **Hungarian transport**.

> Upgrading from a version installed as a HACS **Dashboard** repository?
> Remove that repository first, then add it again as an **Integration**.

The integration loads the card for you. You do **not** need to add a
Lovelace resource by hand.

### 2.2 The BKK API key

BKK (Budapest) and MÁV cards need a free BKK Open Data key. Volán and city
cards work without one.

1. Sign up at <https://opendata.bkk.hu/data-sources> and copy your key.
2. Enter it when you add the integration, or later under
   **Settings → Devices & services → Hungarian transport → Configure**.

The recommended place for the key is the integration. Cards then reach BKK
through Home Assistant, so the key never appears in a dashboard
configuration or in the browser, and several cards share one request.

You can also give one card its own key in the card editor (see
[4.2](#42-the-bkk-key-in-the-editor)). A card's own key wins over the
integration's.

### 2.3 Manual install (card only)

Unzip `hungarian-transport-frontend.zip` from a
[GitHub release](https://github.com/bator/hungarian-transport/releases) into
`/config/www/hungarian-transport/`, then add the resource
`/local/hungarian-transport/hungarian-transport.js` with type **JavaScript
module**. This installs the cards and timetables only, without the MÁV
platform and seat-reservation service.

---

## 3. The integration

### 3.1 Settings

**Settings → Devices & services → Hungarian transport → Configure** opens
the **BKK API key** field and, once a key is stored, a **Remove the stored
key** checkbox.

- Enter a new key and save: the integration reloads and every card uses it.
- Leave the field empty and save to keep the stored key (it is never shown).
  Tick **Remove the stored key** and save to remove it.
- The field hides the key while you type.

When BKK rejects the stored key (revoked or expired), Home Assistant asks
for a new one: a **BKK key rejected** prompt appears under
**Settings → Devices & services**. Enter a new key there, or leave it empty
to switch BKK access off. Until then the cards show the error instead of
sending the rejected key again and again.

### 3.2 Repairs messages

These appear under **Settings → Repairs**.

| Message | Meaning | What to do |
| --- | --- | --- |
| **Frontend assets missing** | The card files are not where the integration expects them. | Reinstall the release from HACS (or the `hungarian-transport.zip` release asset), then restart. |
| **MÁV route index not built** | The MÁV timetable you added could not be turned into train routes. | Add a new feed, upload one, or run the **Rebuild MÁV route index** action. |

### 3.3 Actions

You can try these under **Developer tools → Actions**.

| Action | What it does |
| --- | --- |
| `hungarian_transport.elvira_between` | MÁV departures between two stations, with platforms and seat-reservation flags. Takes two nine-digit MÁV station codes and a look-ahead in minutes (15–480). |
| `hungarian_transport.futar` | A BKK FUTÁR request with the integration's key. The cards use it; it exists only while a key is set. |
| `hungarian_transport.rebuild_mav_index` | Builds the train route index from `/config/hungarian-transport/mav-gtfs.zip` and checks every trip against it. |

### 3.4 Train routes on the map (optional)

Without extra data, a train's map uses a live lookup that sometimes finds
nothing. MÁV publishes its full timetable (GTFS, CC0 licence) to registered
users. With it, every train in Hungary draws its own route.

1. Register at <https://www.mavcsoport.hu/gtfs-igenybejelento>. MÁV emails a
   download address and a login.
2. Download `gtfsMavMenetrend.zip`.
3. Put it at `/config/hungarian-transport/mav-gtfs.zip`, for example with
   the **File editor** app: open (or create) the `hungarian-transport`
   folder, upload the file, rename it to `mav-gtfs.zip`.
4. Run **Rebuild MÁV route index**, or upload the feed. A restart is enough
   only when your feed is newer than the index that came with the release:
   at startup the newer feed wins, and an older or expired feed is not built.

The build checks every trip: each station must lie within 300 m of the
drawn route. If the check fails, the previous index stays in use and a
notification tells you why. For routes that follow the tracks exactly, see
[MÁV train routes](../timetables.md#máv-train-routes).

---

## 4. The departure card

### 4.1 Adding the card

1. Open a dashboard, choose **Edit dashboard → Add card**.
2. Search for **Hungarian transport** and pick it.

The editor opens with the card's live preview next to it. Until you pick two
stops, the preview asks you to.

![The card editor, empty, with the preview asking for two stops](images/en/editor-empty.webp)

The editor fields, from top to bottom:

| Field | What it does |
| --- | --- |
| **Language** | *Automatic* follows your Home Assistant language. You can pin Hungarian or English. |
| BKK API key line | Shows where the key comes from. *Change* opens the key field. |
| **Name (optional)** | The card title. Empty means "origin → destination". The editor fills it in when you pick stops, and keeps a title you typed yourself. |
| **MÁV / Volán / Local** | The mode. None ticked means Budapest BKK. Only one can be on. |
| **City** | Only in Local mode: which city's network. |
| **Origin stop** | Search box, with shortcut chips for the main hubs. |
| **Destination stop** | Only stops that a vehicle from the origin actually reaches. |
| **Look-ahead (minutes)** | How far ahead to list departures. Pick a preset chip from 30 to 480 minutes. |

### 4.2 The BKK key in the editor

When the integration holds the key, the editor only says so (see the picture
above). Without one, a **BKK API key** field appears with a link to the free
sign-up. **Show** reveals what you typed.

![The editor asking for a BKK API key, with the sign-up link](images/en/editor-key.webp)

Once the key works, the field folds away and a **Change** link stays. If BKK
rejects the key, the field comes back with an error.

Without a key in the integration, the editor borrows the key of another card
on the same dashboard for its searches and says so; it does not save that key
into the new card. A key on a card is deprecated: the editor shows a note
while the card's YAML holds one, in every mode.

### 4.3 The four modes

| Mode | Shows | Needs the BKK key |
| --- | --- | --- |
| *(none ticked)* BKK | Budapest buses, trams, trolleybuses, metro, HÉV | Yes |
| **MÁV** | Trains between two stations | Yes |
| **Volán** | Long-distance and regional coaches | No |
| **Local** | One city's own network | No |

### 4.4 Step by step: a MÁV card

Tick **MÁV**, then type part of the station name. The hint under the mode
switches tells you what to pick.

![Searching for the origin station "Kelenföld" in MÁV mode](images/en/editor-mav-search.webp)

Click the station. Then click into **Destination station**: it lists every
station a train from the origin reaches directly. Type to filter the list.

![The list of stations reachable from Budapest-Kelenföld](images/en/editor-mav-dests.webp)

Pick the destination. The card title becomes "origin → destination", and the
preview fills with the next trains.

![The finished MÁV card in the editor, with the live preview](images/en/editor-mav-done.webp)

Click **Save**. The card on the dashboard looks like this:

![MÁV card: trains from Budapest-Kelenföld to Székesfehérvár](images/en/card-mav.webp)

Every train that stops at both stations is listed: suburban trains (Z30,
G43), InterCity trains and named trains (TÓPART, BAKONY) that run further.

### 4.5 A BKK card

With no mode ticked, the card shows Budapest public transport. Here: every
line from Móricz Zsigmond körtér to Fővám tér (M4 metro, trams 47 and 49):

![BKK card: M4 metro and trams from Móricz Zsigmond körtér to Fővám tér](images/en/card-bkk.webp)

A red time means the vehicle runs late; the scheduled time is struck
through next to it.

### 4.6 A Volán card

Tick **Volán** and pick two coach stops. Coach timetables come from the
national GTFS, so they work without a BKK key, and the editor hides the key
line in this mode.

![Volán mode in the editor: Népliget to Kecskemét bus station](images/en/editor-volan-done.webp)

With a 240-minute look-ahead, the card on the dashboard:

![Volán card: coaches from Budapest Népliget to Kecskemét](images/en/card-volan.webp)

### 4.7 A local city card

Tick **Local**, choose the city, then the two stops.

![Local mode with Szeged selected, searching for the origin stop](images/en/editor-city-search.webp)

The **Look-ahead** chips set how far ahead the card lists departures. Here
*60* keeps the frequent tram to one screen:

![Local card in the editor: tram 2 in Szeged, 60-minute look-ahead](images/en/editor-city-done.webp)

The available cities include Miskolc, Pécs, Szeged, Szombathely, Veszprém,
Paks, Dombóvár, Budaörs, Hódmezővásárhely, Kaposvár, Kiskunhalas,
Székesfehérvár, Tatabánya, Várpalota, the Kecskemét narrow-gauge railway,
the Bahart Balaton ferries, and Volánbusz local networks such as Sopron or
Eger.

![Szeged tram 2 from Széchenyi tér to Európa liget](images/en/card-city.webp)

---

## 5. Reading a departure row

![Close-up of a MÁV row with the seat-reservation tooltip](images/en/card-mav-tooltip.webp)

From left to right:

| Part | Meaning |
| --- | --- |
| Vehicle icon | Bus, tram, trolleybus, metro, train or ferry. |
| Line badge | The line in its own colours (Budapest suburban train colours for S, G and Z lines). |
| Headsign | Where the vehicle goes. MÁV rows add the train number in grey. |
| Blue box | The platform (MÁV, from ELVIRA). |
| Outlined code | On a tram, bus or metro row, the stop's stand code from FUTÁR (B1, say). |
| ♿ | Wheelchair accessible, low-floor vehicle. |
| 🚲 | Bikes allowed. |
| **H** | Seat reservation required. The tooltip says when 2nd class needs none on a domestic section. |
| **OB** | Valid with the national pass (Országbérlet). |
| Map pin | Opens the route map (see [section 6](#6-the-route-map)). Clicking anywhere on the row does the same. |
| Time | Departure time. **Red** when late, with the scheduled time struck through; **green** when early. |
| Countdown | Minutes until departure, or *now*. |
| Grey minutes below | Travel time to your destination. |

Hover over (or focus with the keyboard) any mark to see its explanation, as with **H** above.

How the card refreshes:

- Departures reload every 45 seconds (`refresh`, at least 20).
- The countdowns update every 15 seconds without a new request.
- A departure stays for one minute after it was due, then drops off.
- When the browser tab is hidden, the card stops polling and catches up when
  you come back.

---

## 6. The route map

Click a row or its map pin. A map opens with the vehicle's route.

![Map of tram 47 with its live position, boarding and alighting stops](images/en/map-bkk.webp)

| On the map | Meaning |
| --- | --- |
| Coloured line | The trip's full route, in the line's colour. |
| Dot with the line's badge and stop name | Where you board. |
| White ring with the stop name | Where you get off. |
| Round vehicle marker with a halo | Live GPS position. |
| Vehicle marker with a dashed border | Position estimated from the timetable. |

The header names the line, its destination and the vehicle type (and model,
when BKK knows it). The footer sums up the map: route, boarding, alighting,
live or estimated position, how far the vehicle is to your stop, its
expected time and the scheduled one.

Trains draw their own route (best with the MÁV timetable added, see
[3.4](#34-train-routes-on-the-map-optional)):

![Map of a MÁV train from Kelenföld](images/en/map-mav.webp)

Volán coaches and city lines draw the route from the operator's own
timetable data, without any third-party service:

![Map of a Volán coach from Népliget to Kecskemét](images/en/map-volan.webp)

![Map of Szeged tram 2](images/en/map-city.webp)

Close the map with **×** or the **Escape** key. Zoom with the **+** and
**−** buttons, the mouse wheel or two fingers.

---

## 7. The planner

The planner searches every operator at once: BKK, MÁV, Volán and the city
networks. It needs no mode.

### 7.1 Adding the planner

Add the card **Hungarian transit stop planner**. Its editor has the same
fields as the departure card, without the mode switches and the City field.
You can leave the
stops empty and pick them on the card itself.

![The planner's editor](images/en/planner-editor.webp)

### 7.2 Picking places

A new planner has an **Origin** and a **Destination** field. Type a stop,
a street with house number, a town or a place.

![A new, empty planner](images/en/planner-new.webp)

The hits show the town in brackets, and mark addresses and places, so
stops with the same name in different towns stay apart:

![Searching for "Fő tér" lists squares in many towns](images/en/planner-search.webp)

The swap button (⇅) on the right exchanges origin and destination.
**Reset** clears both.

### 7.3 Your own suggestions

Under **Origin**, the planner suggests the stops *you* pick most often. The
letter in front shows the network: **B** BKK, **M** MÁV, **V** Volán,
**H** city network.

![Origin suggestions from the user's own history](images/en/planner-suggestions.webp)

- Every origin and destination you pick counts. Frequent and recent stops
  come first; an old count halves every 30 days.
- The list is stored per Home Assistant user, so your phone and tablet show
  the same suggestions, and other users do not see yours.
- A new user sees no suggestions until they pick a stop.

### 7.4 Now: the live board

With **Now** selected, the planner shows every direct departure between the
two places, in the same layout as the departure card, whichever operator
runs it. **Look-ahead** sets how far ahead it looks. **Map** draws the first
departure's route.

![The planner's live board from Kelenföld to Székesfehérvár](images/en/planner-now.webp)

When there is no direct service, or a route with a transfer arrives
earlier, the planner shows that itinerary under **With a transfer**, with a
small map and every leg on a timeline:

![A transfer itinerary from Budapest-Keleti to Szeged](images/en/planner-transfer.webp)

The timeline lists each walk, wait and ride: the time, the stop, the line
badge and its destination, the ride time, and where you get off.

**Map** opens the large map with every leg in its own colour:

![The full itinerary map](images/en/planner-fullmap.webp)

### 7.5 Depart and Arrive: itineraries for a time

Choose **Depart** or **Arrive**, then set the date and time (five-minute
steps). The planner lists up to six itineraries leaving at or after that
time, or arriving by it. An itinerary that another one beats on both
departure and arrival is left out.

![Itineraries leaving at 16:00, folded](images/en/planner-depart.webp)

Each itinerary shows its departure and arrival times, its duration, the
lines as coloured badges with walks in between, the number of transfers, and
the walking and waiting time. Click one to open it:

![One itinerary opened with its map and timeline](images/en/planner-itinerary.webp)

**Arrive** works the same way, counting back from the time you set. The
itinerary that arrives closest to your time comes first:

![Itineraries arriving in Esztergom by 18:00](images/en/planner-arrive.webp)

The chosen time stays on the card until you change it. It is not saved in
the dashboard configuration; after a reload the planner starts on **Now**.

### 7.6 Across the country

The planner combines every network in one itinerary. From Kossuth tér in
Pécs to Budapest-Keleti, the first itinerary walks to the Pécs coach
station, takes the Volán coach to Budapest, then bus 107 and the M4 metro.
The next ones take city bus 104 to the railway station and the MECSEK
InterCity:

![From Kossuth tér in Pécs to Budapest-Keleti: coach, bus and metro](images/en/planner-cityrail.webp)

---

## 8. Layout, language and themes

### 8.1 Phones and narrow columns

On a narrow card each departure takes two lines, so the headsign stays
readable. The time columns stay aligned.

![The MÁV card at phone width](images/en/card-narrow.webp)

In a **sections** dashboard the card can be resized freely; resizing does
not reload its data.

### 8.2 Language

The cards and editors speak Hungarian and English. With **Automatic** they
follow the Home Assistant user's language. Error messages follow the
language too.

### 8.3 Dark theme

The cards use your Home Assistant theme colours:

![The BKK card with a dark theme](images/en/card-dark.webp)

---

## 9. Keeping timetables current

The Volán and city timetables ship with each release. A weekly GitHub job
rebuilds them, and when a timetable changed it publishes a new patch
release with the same code and the fresh timetables. HACS then offers it as
an ordinary update; a release, once out, never changes.

For daily updates on your own Home Assistant, follow
[Keeping the GTFS indexes current](../timetables.md#keeping-the-gtfs-indexes-current)
in the timetables guide. In short:

1. Copy `scripts/` to `/config/hungarian-transport/`.
2. Copy `packages/hungarian_transport.yaml` into your packages folder.
3. Restart. The update runs every day at 04:30 and five minutes after each
   start, and rebuilds only the feeds that changed.

A failed update keeps the previous timetable and sends a notification (see
[When an update fails](../timetables.md#when-an-update-fails)).

---

## 10. Troubleshooting

![The error line shown when no BKK key is available](images/en/card-nokey.webp)

| You see | Cause | Fix |
| --- | --- | --- |
| *No BKK API key: set it in the Hungarian transport integration or in the card.* | A BKK or MÁV card has no key. | Add the key under the integration's **Configure**, or in the card editor. |
| *The BKK API key is invalid (401/403).* | BKK rejects the key. | Get a new key and enter it in the **BKK key rejected** prompt or under **Configure**. |
| *No upcoming departures.* | Nothing runs in the look-ahead window (often at night). | Raise **Look-ahead**, or check the stops. |
| *No direct service to that destination. Reachable destinations: …* | The destination has no direct service from the origin. | Pick one of the listed destinations, or use the planner. |
| *Could not download the timetable index (timeout or network error).* | The browser could not load the Volán or city timetable. | Reload the page. If it persists, reinstall the release (HACS → Redownload) and restart Home Assistant. |
| *The timetable index is newer (vN) than this card understands.* | The browser still runs an old card. | Reload the page with the cache cleared, or update the integration. |
| No platform, **H** or **OB** marks on MÁV rows | MÁV ELVIRA did not answer. | Nothing to do: the trains are still listed, and the marks come back on their own. |
| A train's map is a straight line between stations | No route data for that train. | Add the MÁV timetable ([3.4](#34-train-routes-on-the-map-optional)). |
| "Custom element doesn't exist" after an update | The browser loaded the page before the new card. | Restart Home Assistant after updating, then reload the page. |
| A coach connection you know of is missing | The official timetable has no such direct service (for example, coaches to Székesfehérvár leave from Népliget, not Kelenföld). | The card shows the official timetable only. Try the planner for a route with a transfer. |

---

## 11. Configuration reference

Everything the editor sets can also be written in YAML.

### Departure card

```yaml
type: custom:hungarian-transport-card
language: auto            # auto, hu or en
mav: true                 # or volan: true, or helyi: true with city: szeged
stopId: BKK_005501024
stopName: Budapest-Kelenföld
destKey: szekesfehervar
destName: Székesfehérvár
minutesAfter: 180         # 15–480
refresh: 45               # seconds, at least 20
```

| Option | Default | Description |
| --- | --- | --- |
| `language` | `auto` | `auto`, `hu` or `en`. |
| `name` | origin → destination | Card title. |
| `apiKey` | — | The card's own BKK key. Optional when the integration holds one. |
| `mav` / `volan` / `helyi` | `false` | Mode. At most one is on; none means BKK. |
| `city` | — | City network id for `helyi` (`szeged`, `pecs`, `miskolc`, `szombathely`, …). |
| `stopId`, `stopName` | — | Origin, set by the editor. |
| `destKey`, `destName` | — | Destination, set by the editor. |
| `routeIds` | filled by the editor | Lines that serve the destination. |
| `minutesAfter` | `180` | Look-ahead in minutes (15–480). |
| `refresh` | `45` | Seconds between reloads (at least 20). |
| `favorites` | main hubs | Your own shortcut chips: a list of `{ id, name }`. |
| `volanIndex`, `cityIndex`, `mavIndex` | next to the card | Other timetable index URLs. |

### Planner

```yaml
type: custom:hungarian-transit-stop-card-plan
language: auto
minutesAfter: 180
```

The planner takes the same `language`, `name`, `apiKey` and `minutesAfter`
options. Origin and destination can be set in the editor or left to the
card.
