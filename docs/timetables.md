# Timetables, train routes and updates

Reference for running Hungarian transport: where the timetable data comes
from, how to add the MÁV feed for train routes, and how the indexes are kept
current. The [user manual](manual/README.md) covers the cards themselves.

## Contents

- [MÁV train routes](#máv-train-routes)
- [Live positions and route shapes](#live-positions-and-route-shapes)
- [Keeping the GTFS indexes current](#keeping-the-gtfs-indexes-current)
- [When an update fails](#when-an-update-fails)
- [Feed sources](#feed-sources)
- [Card-only install and building the indexes](#card-only-install-and-building-the-indexes)

## MÁV train routes

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
  lie within 300 m of the route. An index with fewer than 50 trains, or with
  fewer than 99% of them passing, is not written: the previous index stays in
  use and a notification says so. The result is in the action's response and
  in the log.
- **Rebuilt when needed.** A HACS update replaces the card's folder, so the
  index is rebuilt at startup whenever it is missing or was built from another
  feed (by the feed's `feed_version`). A feed whose build failed is not built
  again at every start; its Repairs issue comes back instead, and the
  `rebuild_mav_index` action forces a new try.

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

## Live positions and route shapes

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

## Keeping the GTFS indexes current

HACS installs the indexes that were built when the GitHub release was cut,
and a published release never changes afterwards. A weekly GitHub Action
rebuilds the indexes on the latest stable release's code (the highest
`X.Y.Z` by version, not the newest by date); when a feed
changed, it publishes them as the next patch release (1.9.12 → 1.9.13),
tagged on that same commit, so HACS offers the new timetables as an
ordinary update and no unreleased code goes out with them. When nothing
changed, nothing is published: the indexes are compared by content (every
trip with its stops, times, service days and route shape), not by their
bytes, so a rebuild that only lists the same trips in another order is no
change.

Version numbers keep the timetable releases and the live-Lovelace
prereleases apart. A `rev` run publishes main as `X.Y.(Z+2)-rev.N` after
stable `X.Y.Z` (1.9.15-rev.1 after 1.9.13), leaving the next patch (1.9.14)
to the weekly timetable release, which semver ranks below the prerelease:
beta users stay on main. A later `rev` run continues an open series, or
starts one two patches above a newer stable. A `patch` run publishes main
under the open series' number (1.9.15), so what the beta ran ships under
its own version; without an open series it is the next patch. While a
series is open, every weekly timetable release is followed by a `rev` run
from main that the workflow starts itself: main with the fresh indexes,
two patches above the new stable, so the beta channel stays above it on
main's code. That run is a prerelease, not a refresh, and starts nothing
further. Only if it failed does the next weekly release find the open
series on the very next patch; it then takes the number above the series
and warns: beta users are offered that release's older code until a `rev`
run is published. The version is picked from GitHub's list of releases and
tags; when GitHub does not answer, the run stops instead of guessing. A release run compares the new indexes with
the latest published ones: a city feed that is gone, a feed that lost more
than half of its trips, or a Volán index with far too few trips fails the
run. Trips whose own service has ended (a summer timetable in October) do
not count as lost: the comparison is on the trips that still run, and when
those are under a quarter of the old index, the new one has to keep 90% of
them (under 50 still running: all trips). A failed run makes no release
(see [When an update fails](#when-an-update-fails)).
To accept a feed that is gone for good, list its id in the `allow_missing`
input of a manual run, or in the `HUNGARIAN_TRANSPORT_ALLOW_MISSING`
repository variable for every run including the weekly one (the input adds
to the variable), and drop it from `CITY_FEEDS`.
Between releases, the local rebuild below keeps an install current every day.
Every release also publishes `SHA256SUMS`, `hungarian-transport-build.json`
and a build provenance attestation: check a download with
`sha256sum -c SHA256SUMS`
(next to the zips) or
`gh attestation verify hungarian-transport.zip --repo bator/hungarian-transport`.
The attestation names the commit of the workflow run that built the zip. On
a weekly timetable release that is the head of `main` (where the workflow
runs), not the tagged commit the zips are built from;
`hungarian-transport-build.json` gives both, `built_commit` (the tag) and
`workflow_commit` (the run).

To rebuild them yourself on Home Assistant OS:

1. Copy `scripts/*.py` and `scripts/run_update.sh` to `/config/hungarian-transport/`
   (`build_mav_index.py` included). pfaedle output stays on the builder host,
   not on Home Assistant OS — without it the official zip is stop-sequence,
   not OSM rails.
2. Copy `packages/hungarian_transport.yaml` into a Home Assistant package
   directory (`packages: !include_dir_named packages`).
3. Restart Home Assistant. The automation runs at 04:30 in Home Assistant's
   own time zone (Settings → System → General) and five minutes after a core
   start. It HEADs the KTI zip and the municipal feeds (over https; plain http
   only for the city feeds marked insecure) and rebuilds only when the
   fingerprint changed. A server that gives no usable fingerprint is
   downloaded, and a zip byte for byte the same as the one the index was
   built from is no change either. A new Volán zip replaces the kept
   `volanbusz_gtfs.zip` only once its index built. The run writes
   `volan-index.json.gz` / `city-index.json.gz` atomically into
   `/config/custom_components/hungarian_transport/www/`, where the integration
   serves the card from (the old `/config/www/community/hungarian-transport/`
   folder is used only when the integration is not installed). A durable copy
   is also kept under `/config/hungarian-transport/` so a HACS update that
   overwrote the integration folder is restored on the next run, unless the
   release ships a newer index schema than your copied scripts build. Such
   scripts do not rebuild that index either: the run skips it, keeps the
   shipped one and says *… kihagyva: … has index schema N, these scripts
   build M* until you copy the new `scripts/` over. For
   `mav-index.json.gz`, of two builds of the same feed the larger
   (pfaedle-based) one wins, otherwise the later build, so an index the
   integration built from an uploaded zip is kept. A local `mav-gtfs.zip` or
   pfaedle directory also rebuilds `mav-index.json.gz`; with `MAV_GTFS_URL`
   set, each daily run downloads the zip again once it is 20 hours old. A MÁV
   index that fails its audit is not written. Volán, city and MÁV fail
   independently: each failed rebuild notifies and keeps that index as it
   was, and the rest of the run goes on. A city feed that failed is
   downloaded and rebuilt again on the next run; one left out because its
   calendar ended is not, until the publisher posts a new zip. A feed that
   fails to download keeps its last zip; once it has failed three days in a
   row the webhook says so, then weekly while it keeps failing (the same for
   `MAV_GTFS_URL`). The shapes folders travel with their indexes.
   Simplifying the Volán `shapes.txt` (8 million points) takes about a
   minute on a desktop and longer on a Raspberry Pi; `run_update.sh` allows
   30 minutes.

Set `HUNGARIAN_TRANSPORT_WWW` in `/config/hungarian-transport/.env` if you
installed the card somewhere else, for example
`/config/www/hungarian-transport` for the manual `/local` install.
`HUNGARIAN_TRANSPORT_WEBHOOK` there sends the update's messages, those of
`run_update.sh` included, to another webhook URL. That file wins over a
`/config/.env`; `export KEY=value` and `# comments` after a value are fine.
Point `notify.admins` at whatever notifier you use, or drop that automation.

## When an update fails

The local update sends its failures to the webhook (and so to
`notify.admins`); `run_update.sh` sends the ones the script cannot report
itself. `/config/hungarian-transport/update.log` has the details.

- *… frissítés sikertelen: …* — a feed did not download, did not parse or
  failed a guard. That index stays as it was and the next run tries again.
  A city feed or `MAV_GTFS_URL` that does not download is only reported once
  it has failed three days in a row (`download failing: <id> since …`, then
  weekly): its URL has probably moved. The last zip stays in the index
  until its calendar ends.
- *Új menetrend: … (skipped: …)* — the index was rebuilt without these
  feeds: they failed to download or parse, and are tried again next run.
- *… frissítés kihagyva: … has index schema N, these scripts build M* — a
  release shipped a newer index format than the copied scripts write; the
  shipped index stays. Copy all of `scripts/` again.
- *… el sem indult* or *… összeomlott (exit N)* — the script could not start
  or crashed. Usually `/config/hungarian-transport/` holds a half-copied set
  of scripts: copy all of `scripts/` again.
- *… megszakítva* or *… megölve* — Home Assistant restarted during the run,
  the 30-minute limit hit, or the host ran out of memory.

The city index (locally and in a release run) is not replaced when the
rebuild would lose a feed that the index in place still runs, or more than
half of a feed's trips. Each feed is judged on its own. A feed whose trips
in the index have all ended has nothing left to lose; one that still runs is
compared on the trips that still run (a trip counts while its own service
has days left), on both sides, as long as they are at least a quarter of its
trips and at least 50: a feed whose summer service ended is compared on its
autumn trips alone. When only a handful still run, all its trips are the
yardstick, so a feed that really shrank is refused then too. There is no
check on the index's total, so feeds whose calendars end together (several
on 31 December) leave without holding back the rest. The Volán index is a
single feed, compared the same way: it is refused with fewer than half the
trips of the one in place. The message names the feed:

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

## Feed sources

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
  See [MÁV train routes](#máv-train-routes).
- National Access Point (SZKT official dump and others): <https://napportal.kozut.hu/>
- Kecskemét KEKO city buses: <https://timetable.keko.hu/Kecskemet_GTFS.zip> (auth)

BKK static GTFS is public, but Budapest already uses live FUTÁR, so that zip
is not folded into `city-index.json.gz`. The BKK Open Data key is only for
FUTÁR / GTFS-RT.

## Card-only install and building the indexes

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
not ship them. For the local daily rebuild, set
`HUNGARIAN_TRANSPORT_WWW=/config/www/hungarian-transport`.
