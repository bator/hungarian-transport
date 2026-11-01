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

MÁV publishes its timetable (GTFS, CC0 licence) only to registered users.
Releases ship a MÁV index matched to the OSM rails with pfaedle when the
repository that builds them has a MÁV login (see
[Releases with the MÁV index](#releases-with-the-máv-index)); such an
install needs nothing more. Between releases the integration fetches the
same index built every morning from MÁV's newest feed
([The daily index](#the-daily-index-from-github-pages)), so the routes are at
most a day behind MÁV, not a week. Without an index a train's map relies on a live
Transitous lookup, which sometimes finds no trip, and on BKK FUTÁR, which
only knows trains around Budapest. With one, every train in Hungary draws
its own route.

To build it on your own Home Assistant instead (a fork's release without
the secrets, the daily index turned off, or a feed of your own):

1. Register at <https://www.mavcsoport.hu/gtfs-igenybejelento>. MÁV emails
   the download address and a login.
2. Download the zip (`gtfsMavMenetrend.zip`).
3. Put it at `/config/hungarian-transport/mav-gtfs.zip`. With the
   **File editor** app: open the `hungarian-transport` folder (create it if
   missing), choose *Upload file*, then rename the file to `mav-gtfs.zip`.
4. Run the `hungarian_transport.rebuild_mav_index` action, or upload the
   feed. A restart is enough only when your feed is newer than the index
   that came with the release (see *The release's index* below).

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
- **The release's index.** Without a local feed the index a release shipped
  is used as it is: nothing is built and nothing is reported. With one, the
  newer feed wins at startup. A feed's date is the one its `feed_version`
  starts with (`20261022.M2ChangeID`), else its `feed_start_date`, else its
  `feed_end_date`. An index of the local feed's own `feed_version` is kept,
  whoever built it ([Which index wins for the same
  feed](#which-index-wins-for-the-same-feed)), and so is one from a newer
  feed: the log says once that the local feed was not used. A shipped index of an older feed is
  replaced by a build of the local feed, once after each update. A local
  feed that cannot be dated is built, as is one of the same date. A local
  feed past its `feed_end_date` is never built over a shipped index.
- **You choose.** An upload or the `rebuild_mav_index` action always builds
  the local feed, whatever its age. That index stays at the next starts; the
  next update that ships a newer feed's index replaces it again, and so does
  the daily index once it is of a newer feed than the one you uploaded.

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

### Which index wins for the same feed

One `feed_version` can be indexed twice: from a pfaedle-matched feed
(releases, the daily index) and from MÁV's official zip, which mostly gives
the line through the stations. Every place that may replace an index with
another of the same feed (the startup check, the daily index, the local
update script) follows one rule (`mav_index.same_feed_replaces`):

1. An index is *pfaedle-fitted* when at least 90% of its variants follow a
   shape rather than their station line (read from the index itself; the
   release guard `MAV_MIN_FITTED` publishes none below that).
2. A fitted index is replaced only by another fitted one more than 4 KiB
   larger: never by a stop-sequence index, nor by a build not made yet.
3. A stop-sequence index is replaced by a fitted one, and by another
   stop-sequence one more than 4 KiB larger.
4. A build not made yet (its kind and size unknown) replaces no index of its
   own feed. So startup does not rebuild a feed that was only copied again,
   and the update script does not build MÁV's zip over the daily index of
   the same feed; a pfaedle directory (`MAV_PFAEDLE_DIR`) counts as fitted
   and may replace a stop-sequence index. An upload or the
   `rebuild_mav_index` action always builds.

**Updating automatically.** An admin can upload the zip instead of copying
it. The response contains the build and check results:

```bash
curl -X POST -H "Authorization: Bearer $HA_TOKEN" \
  --data-binary @mav-gtfs.zip \
  http://homeassistant.local:8123/api/hungarian_transport/mav_gtfs
```

A daily job that downloads (and pfaedle-matches) the feed can end with this
call. With the [daily index](#the-daily-index-from-github-pages) on (the
default) such a job is no longer needed to keep the routes current: it only
matters for a feed of your own, or with the daily index turned off.

### Releases with the MÁV index

The release workflow builds `mav-index.json.gz` when the repository has the
MÁV login as Actions secrets. Settings → Secrets and variables → Actions:

| Name | Kind | Value |
| --- | --- | --- |
| `MAV_GTFS_USER` | secret | the user name from MÁV's e-mail |
| `MAV_GTFS_PASSWORD` | secret | its password |
| `MAV_GTFS_URL` | variable, optional | the zip's address; default `https://www.mavcsoport.hu/gtfs/gtfsMavMenetrend.zip` |
| `VOLAN_GTFS_URL` | variable, optional | another Volán zip address (see [Feed sources](#feed-sources)) |

With both secrets set, every release run (the weekly timetable release
included) downloads MÁV's zip over https with HTTP Basic auth (the
credentials go to a netrc file for that host only, never onto a command
line or into the log), checks its length and zip directory, matches it to
the [Geofabrik](https://download.geofabrik.de/europe/hungary.html) Hungary
extract (cached for a week) with a pinned pfaedle image, and builds and
audits the index with `scripts/build_mav_index.py`. About a minute of
pfaedle; the run summary shows the trains, lines and audit. The index ships
only when it passes the audit (99% of the trains) and at least 90% of its
stop patterns follow their pfaedle route: an index on MÁV's own coarse
shapes is never released. A refused login (HTTP 401/403) is not retried,
so wrong credentials cost one login per run; pfaedle gets 30 minutes. When
the download, pfaedle or the audit fails, and on a run without the secrets,
the release still goes out with the previous release's MÁV index (when the
card still reads its format), the failure with a warning. A repository
whose releases never had one (a fork) ships none. The weekly release counts
a changed or new MÁV index (compared by content, not bytes) as a timetable
change, so a new MÁV timetable alone publishes a patch; an index that is
not in a build never does. The OSM extract is cached only after a build
that went through. The **MÁV index** workflow runs the same pipeline on a
pull request that touches it, or by hand, builds the index a second time
from the same downloaded zip to check that it is reproducible, and keeps it
as a 3-day artifact without publishing anything.

### The daily index from GitHub Pages

MÁV publishes a new feed almost every day; releases come once a week. The
**Daily MÁV index** workflow (`.github/workflows/mav-index-daily.yml`) runs
the release's pipeline above every morning at 03:15 UTC, after MÁV's nightly
update (the site is ready before 05:30 Hungarian time), and publishes on
GitHub Pages, under `https://bator.github.io/hungarian-transport/`:

| File | What it is |
| --- | --- |
| `mav-index.json.gz` | the integration's train route index |
| `mav-index.json` | the manifest (below) |
| `mav-gtfs.zip` | MÁV's official GTFS, byte for byte as downloaded for this build (CC0) |
| `mav-pfaedle-overlay.zip` | pfaedle's `shapes.txt`, `stop_times.txt` and `trips.txt` of the same feed |
| `ATTRIBUTION.txt`, `index.html` | what the files are, sources and licences |

The overlay holds the three tables pfaedle rewrites when it matches every
train to the OpenStreetMap rails: use them in place of the official feed's
tables of the same names (for a GTFS consumer such as Dawarich, or anything
else). It is zipped deterministically (sorted names, fixed timestamps,
deflate), so an unchanged build gives the same bytes. The site is about
35 MB.

**Attribution.** The MÁV GTFS is published by MÁV under CC0 1.0. The shapes
(in the overlay and in the route index) are derived from OpenStreetMap data:
© OpenStreetMap contributors, Open Database License (ODbL) 1.0; credit them
when you publish anything built on these files.

The manifest names the manifest `format` (1), the index `schema`, the `feed`
(`feed_version`), `built_at` (UTC), `size`, `sha256`, the content `digest`
(`scripts/index_digest.py`), trains and lines, the audit (trains and trips
that passed), the `commit` it was built from, and a `gtfs` object (sizes
here are examples):

```json
"gtfs": {
 "feed_version": "20261101.M2ChangeID",
 "official": {"name": "mav-gtfs.zip", "size": 31000000, "sha256": "…"},
 "overlay": {"name": "mav-pfaedle-overlay.zip", "size": 4000000, "sha256": "…",
             "files": ["shapes.txt", "stop_times.txt", "trips.txt"]},
 "attribution": "ATTRIBUTION.txt"
}
```

`gtfs.feed_version` is the official feed's, always the index's `feed`. A
consumer can fetch the small manifest first and download the zips only when
`feed_version` or a `sha256` changed.

Nothing is published, and the site keeps the previous day's files, when the
secrets are missing, the download, pfaedle or the audit fails (the run shows
a warning), or the index and both zips have the same content as the ones
already published.

The integration reads the manifest about 10 to 20 minutes after Home
Assistant starts (and after the options are saved, which reloads the
integration, so a changed option takes effect the same day), then once a
day at the **Daily check time** of its options (06:00 local time unless
changed, plus up to 5 minutes), and installs the index only when all of
these hold:

- the manifest's format and the index schema are the ones this version reads
  (a newer one is skipped, with one line in the log);
- its feed is the one of the index in use and the [same-feed
  rule](#which-index-wins-for-the-same-feed) lets it replace that one (a
  stop-sequence build of the feed is replaced, a fitted one stays); or its
  feed is newer, by the date its `feed_version` starts with, than the index
  in use, and newer than a local `mav-gtfs.zip` by the rule above: a local
  feed of the same date or newer, or one that cannot be dated, keeps its
  own index, but only while that index is the one in use (built from the
  feed as it is now, or of its `feed_version`). A local feed whose build
  failed, was interrupted or refused by the audit keeps nothing;
- the download has the manifest's size and sha256, has no data after its
  gzip stream, unpacks to that schema and feed, and has at least 50 trains
  whose routes and stations all resolve.

The index is fetched as `mav-index.json.gz?sha=<the manifest's sha256>`, so
the Pages cache (`max-age=600`) never pairs a fresh manifest with the
previous index. It is written next to the card the way a build writes it (a
temp file, then a rename) and never while a local build runs or waits. A
failed round, or one that found a build running, is tried again 20 minutes
later, up to 6 times until the next daily round; a daily round that finds a
manifest older than 20 hours (the workflow ran late) looks once more one to
two hours later. A failed round leaves the index as it is and logs one
warning, not one per round; no Repairs issue. Open dashboards pick the new index up at their next
reload (the card revalidates it, and Home Assistant answers with a new ETag).

**Opting out.** Settings → Devices & services → Hungarian transport →
Configure → *Daily MÁV route index from GitHub*. Off, Home Assistant makes no
request to GitHub Pages and keeps the weekly release's index (or the one it
builds from your feed).

**Privacy.** With the option on, Home Assistant contacts `bator.github.io`
(GitHub Pages) about once a day: a plain HTTPS GET of the manifest and the
index (never the GTFS zips),
no cookies, nothing beyond what any HTTP request carries (your IP address,
and Home Assistant's user agent with its version). Nothing about your
configuration is sent.

**Notifications.** With *Notify about timetable updates* on in the same
options, the integration creates a persistent notification (one per kind; a
new one replaces the old) when:

- the daily MÁV index was installed (the feed's date, trains and lines).
  MÁV publishes almost daily, so this one calls the service below only with
  *Also notify about daily MÁV index installs* on;
- after an update of the integration, the indexes it ships (Volán, city,
  MÁV) are of other feeds than the ones it saw before, naming which. The
  first run only records them; changes the integration makes itself (the
  daily index, a local build) are not news, nor a shipped MÁV index older
  than the daily one in use;
- the daily index failed on 3 consecutive days: once, and the notification
  goes away when a round succeeds again. A day with only rounds that found
  a build running (or a manifest this version does not read) neither counts
  nor breaks the streak; a day without any round (Home Assistant off) starts
  it over. A round that went through while the manifest was built more than
  3 days ago counts as a failure: the site is no longer rebuilt. The
  notification is shown again after a restart while its streak is open.

*Also call this service* (`domain.service`, for example `script.ertesit` or
`notify.mobile_app_iphone`) is called with `title` and `message` along with
each notification (a daily install only with the option above). The call
waits until Home Assistant has started: at the first start after an update
scripts and `notify.mobile_app_*` are not registered yet when the
integration sets up. The options form lists the scripts (by their name,
also when the entity was renamed) and the `notify.*` services (not
`notify.notify`, `notify.send_message` or `notify.persistent_notification`),
and takes a typed name as well; it refuses a malformed or missing service,
and a typed `None` (only the list's **— none —** means none). A stored
service that is gone is listed as *(missing)* and kept, so the other
options can still be saved. A service that fails later is logged, never
retried. The texts are
Hungarian when Home Assistant's language is Hungarian, else English.

**For forks.** The integration always reads `bator.github.io`. A fork's
workflow publishes on its own Pages site; enable Settings → Pages → *Source:
GitHub Actions* first, or its deploy job stops with an error saying so.

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
   not OSM rails. To download MÁV's zip there, set `MAV_GTFS_URL`,
   `MAV_GTFS_USER` and `MAV_GTFS_PASSWORD` in
   `/config/hungarian-transport/.env` (HTTP Basic over https only, never
   sent on after a redirect, never logged). A release's or the daily
   pfaedle index of the same feed beats such a stop-sequence build, which is
   then not built at all ([the same-feed
   rule](#which-index-wins-for-the-same-feed)).
2. Copy `packages/hungarian_transport.yaml` into a Home Assistant package
   directory (`packages: !include_dir_named packages`).
3. Restart Home Assistant. The automation runs at 04:30 in Home Assistant's
   own time zone (Settings → System → General) and five minutes after a core
   start. It HEADs the Volán zip (`opendata.utas.hu`, then KTI when that
   fails, or `VOLAN_GTFS_URL`) and the municipal feeds (over https; plain http
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
   pfaedle directory also rebuilds `mav-index.json.gz`, unless the served
   index is of the same `feed_version` and the [same-feed
   rule](#which-index-wins-for-the-same-feed) keeps it; with `MAV_GTFS_URL`
   set, each daily run downloads the zip again once it is 20 hours old. A MÁV
   index that fails its audit is not written. Volán, city and MÁV fail
   independently: each failed rebuild keeps that index as it was, and the
   rest of the run goes on. A city feed that failed is
   downloaded and rebuilt again on the next run; one left out because its
   calendar ended is not, until the publisher posts a new zip. A download
   shorter than its `Content-Length`, or a zip without a readable directory,
   is a failed download. A new city or Volán zip the index does not take
   leaves the zip in place: one that does not load is downloaded again next
   run; one the feed guard refuses (no trips, its calendar ended, most of its
   trips gone; for Volán fewer than 10,000 coach trips) is not, while the
   publisher serves it, for up to 7 days and not past the last service day
   of the city zip in place, then it is built again. A MÁV feed whose index
   fails its audit or is too small is likewise not built again for 7 days
   while it is the same feed; a MÁV build that crashed is tried again next
   run, a HACS update in between included (the feed is compared with the
   index's build time, not with when HACS unpacked it). A feed that fails to download, or whose new zip (MÁV feed) the index
   does not take, keeps its last zip; once that has gone on three days in a
   row the webhook says so, then weekly while it lasts (the same for
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
  failed a guard. That index stays as it was.
  A city feed or `MAV_GTFS_URL` that does not download, and a city, Volán
  (`volan`) or MÁV (`mav-build`) feed whose new zip the index does not take,
  is only reported once that has gone on three days in a row
  (`download failing: <id> since …`, then weekly): a URL that does not
  download has probably moved. The last zip stays in the index until its
  calendar ends. `last new zip rejected` or `last new zip does not load`
  means the feed downloads, but the index keeps the old zip;
  `update.log` says why.
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
above. Locally it also lets a named feed's new zip replace the one in place
when the guard would refuse it (a timetable cut on purpose).

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

The Volán feed comes from <https://opendata.utas.hu/public-gtfs/volanbusz_gtfs.zip>,
the address MÁV announced for it, and from
<https://gtfs.kti.hu/public-gtfs/volanbusz_gtfs.zip> (the same zip) when
that fails. `VOLAN_GTFS_URL` (in `.env`, or as a repository variable for
the release) replaces the first; KTI stays the fallback. An `http://`
address there is fetched over https.

These feeds still need a form or login:

- MÁV / GYSEV rail GTFS: <https://www.mavcsoport.hu/gtfs-igenybejelento>.
  See [MÁV train routes](#máv-train-routes); releases build it with the
  repository's MÁV secrets.
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
