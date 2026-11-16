# Timetables, train routes and updates

Reference for running Hungarian transport: where the timetable data comes
from, how to add the MÁV feed for train routes, and how the indexes are kept
current. The [user manual](manual/README.md) covers the cards themselves.

## Contents

- [MÁV train routes](#máv-train-routes)
- [Live positions and route shapes](#live-positions-and-route-shapes)
- [Keeping the GTFS indexes current](#keeping-the-gtfs-indexes-current)
- [Timetable sensors](#timetable-sensors)
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
([The daily MÁV index](#the-daily-máv-index)), so the routes are at most a
day behind MÁV, however long ago the release was cut. Without an index a train's map relies on a live
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
call. With the [daily index](#the-daily-máv-index) on (the
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

With both secrets set, every release run downloads MÁV's zip over https
with HTTP Basic auth (the credentials go to a netrc file for that host only,
never onto a command line or into the log), checks its length and zip directory, matches it to
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
whose releases never had one (a fork) ships none. The OSM extract is cached
only after a build that went through. The **MÁV index** workflow runs the
same pipeline on a pull request that touches it, or by hand, builds the index a second time
from the same downloaded zip to check that it is reproducible, and keeps it
as a 3-day artifact without publishing anything.

### The timetables on GitHub Pages

MÁV publishes a new feed almost every day, and the Volán and city
publishers every few days or weeks; a release comes only with new code. The
**Daily timetables (GitHub Pages)** workflow
(`.github/workflows/mav-index-daily.yml`) publishes all three on GitHub
Pages, under `https://bator.github.io/hungarian-transport/`, so that new
timetables never need a new version: the MÁV index on every run, the Volán
and city indexes once a week ([below](#the-volán-and-city-indexes)). It runs
twice a day, at 04:45 and 14:20 UTC.
In November 2026 MÁV's feed of the day appeared between about 03:20 and
10:00 UTC, once only in the afternoon, and GitHub started the scheduled runs
hours late (one set for 03:15 started between 10:00 and 11:30 UTC every
day). So the 04:45 run usually builds the day's feed, and the 14:20 one
catches a feed MÁV publishes in the afternoon. A run started by hand
(`workflow_dispatch`) comes on top; one run goes at a time and one waits, and
the times are chosen so that at most one scheduled run can meet a dispatch
sent between 09:30 and 11:30 UTC, so a waiting run is never cancelled by a
third. A run that finds nothing new to publish deploys nothing.
The files:

| File | What it is |
| --- | --- |
| `mav-index.json.gz` | the integration's train route index |
| `mav-index.json` | the MÁV manifest (below) |
| `mav-gtfs.zip` | MÁV's official GTFS, byte for byte as downloaded for this build (CC0) |
| `mav-pfaedle-overlay.zip` | pfaedle's `shapes.txt`, `stop_times.txt` and `trips.txt` of the same feed |
| `timetables.json` | the Volán and city manifest ([below](#the-volán-and-city-indexes)) |
| `volan-index.json.gz`, `city-index.json.gz` | the Volán and city indexes, byte for byte as the release's builders write them |
| `volan-shapes-<hash>/`, `city-shapes-<hash>/` | each index's route shapes: the folder its head names (`"sh"`), 64 files `00.json.gz` to `63.json.gz` |
| `ATTRIBUTION.txt`, `index.html` | what the files are, sources and licences, for the whole site |

The overlay holds the three tables pfaedle rewrites when it matches every
train to the OpenStreetMap rails: use them in place of the official feed's
tables of the same names (for a GTFS consumer such as Dawarich, or anything
else). It is zipped deterministically (sorted names, fixed timestamps,
deflate at level 9), so the same three tables give the same bytes; pfaedle
itself writes slightly different tables on every run, even for the same feed,
so the overlay's bytes do not decide whether to publish (below). The MÁV
files are about 35 MB; a site over 500 MB (half of GitHub Pages' 1 GB limit)
is not published, and the run's log lists every file's size.

**Attribution.** The MÁV GTFS is published by MÁV-csoport under CC0 1.0;
`ATTRIBUTION.txt` names MÁV-csoport (<https://www.mavcsoport.hu/gtfs>) as
its source. The shapes (in the overlay and in the route index) are derived
from OpenStreetMap data: © OpenStreetMap contributors, Open Database License
(ODbL) 1.0; credit them when you publish anything built on these files.
The Volán and city indexes are built from the feeds in [Feed
sources](#feed-sources), under their publishers' terms; `ATTRIBUTION.txt`
and `index.html` name those sources and licences as well (CC BY 4.0 for
Máté Gyöngyösi's feeds: credit them too).

**Where the CC0 statement comes from.** From MÁV-csoport's GTFS page,
<https://www.mavcsoport.hu/gtfs>, which sits behind the same registered
login as the feed (<https://www.mavcsoport.hu/gtfs-igenybejelento>; without
one it answers HTTP 401). The site republishes `mav-gtfs.zip` on that
statement alone, and nothing checks it automatically: the repository owner
should keep it verified and stop publishing the zip if MÁV's terms change.

**Checking a download.** Each deploy attests the files that run built (a
build provenance attestation, as for the release zips), taken from the very
files Pages deployed: `timetables.json` every time; `mav-index.json.gz`,
`mav-index.json`, `mav-gtfs.zip` and `mav-pfaedle-overlay.zip` when the run
built a new MÁV part; `volan-index.json.gz` or `city-index.json.gz` and the
shards of its shapes folder when the run built that index. A file carried
over from the live site is not attested again: it keeps the attestation of
the run that built it. Attesting it again would vouch for bytes the run
took from Pages rather than from its own build, so a file that reached
Pages some other way (a run dispatched from another branch, say) could
never pick up a `refs/heads/main` attestation by being carried:

```sh
gh attestation verify mav-gtfs.zip --repo bator/hungarian-transport \
  --signer-workflow bator/hungarian-transport/.github/workflows/mav-index-daily.yml \
  --source-ref refs/heads/main
gh attestation verify volan-index.json.gz --repo bator/hungarian-transport \
  --signer-workflow bator/hungarian-transport/.github/workflows/mav-index-daily.yml \
  --source-ref refs/heads/main
```

`--source-ref` pins the branch: a run started by hand from another branch
signs with a workflow file of the same name, and its files do not pass.

The attestation is made after the deploy, in a job of its own, so a
Sigstore outage never holds back the day's files; the run is then red, and
the files it deployed have none (the check fails, it never passes) until a
later run deploys again, or "Re-run failed jobs" attests them while the
run's Pages artifact is kept (three days). That re-run repeats only the
attestation, never the deploy. It waits in the workflow's concurrency group
like any run, and made while another run waits there, it cancels that one:
re-run when no run of the workflow is queued.
The integration does not check the attestation: Home Assistant has no
Sigstore client, and the integration takes no new dependency for one. It
checks each file against its manifest's size and sha256, which come from
the same site, so it trusts GitHub Pages and this repository's workflow.

#### The daily MÁV index

`mav-index.json`, the MÁV manifest, names the manifest `format` (1), the
index `schema`, the `feed` (`feed_version`), `built_at` (UTC), `size`,
`sha256`, the content `digest` (`scripts/index_digest.py`), trains and
lines, the audit (trains and trips that passed), the `commit` it was built
from, and a `gtfs` object (sizes here are examples):

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

No new MÁV index is published, and the site keeps the previous day's MÁV
files (a deploy for new Volán or city indexes carries them over), when the
secrets are missing, the download, pfaedle or the audit fails, the site is
refused (a feed whose `feed_version` does not start with a real date, a site
over 500 MB), or the index and MÁV's zip are the ones already published
(the overlay's bytes are not compared) and the published manifest is under
12 hours old; older, the same content is published again for its new
`built_at`. A failure is a warning while the published manifest's
`built_at` is less than 2 days old; the run fails (red) when it is 2 days
old or older, or names no build time, and at once when MÁV refuses the login
(HTTP 401/403, which is never retried: only new credentials help). A
published manifest that cannot be read leaves the run green with a warning.

The integration reads `mav-index.json` about 10 to 20 minutes after Home
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

The Pages cache (Fastly, `max-age=600`) keys on the path alone, ignoring a
query string and `Cache-Control: no-cache`, so for up to ten minutes after a
deploy it can hand out the new manifest with the previous index, or the
other way round. A download that does not match its manifest (size or
sha256) is therefore looked at again 11 minutes later with only a debug
line, and is not a round; only the same manifest's index failing to match
again is a failed round. Both files come from `https://bator.github.io/hungarian-transport/`
only, and no redirect is followed: should the site move (GitHub Pages
redirects every `github.io` URL once a custom domain is set), each round
fails with a warning naming the redirect until an update of the integration
reads the new address. The index is written next to the card the way a
build writes it (a temp file, then a rename) and never while a local build
runs or waits. A failed round, or one that found a build running, is tried
again 20 minutes later, up to 6 times until the next daily round. The day's feed is on Pages
around midday or in the afternoon (above), so a round that finds the
manifest's feed of an earlier day (or the manifest built over 20 hours ago)
looks again two to three hours later, up to 4 times after the daily (or the
startup) round and never past midnight: from 06:00, the last look is in the
afternoon. Each look is one request for the small manifest; the index is
downloaded only when its feed is newer. A failed round leaves the index as it is and logs one
warning, not one per round; no Repairs issue. Open dashboards pick the new index up at their next
reload (the card revalidates it, and Home Assistant answers with a new ETag).

#### The Volán and city indexes

The Volán and city indexes are rebuilt once a week, not on every run: their
publishers change a timetable every few days or weeks, and a rebuild
downloads every city feed again. The `timetables` job of a run rebuilds both
when the published `timetables.json`

- is missing (HTTP 404: the site's first such run, or a fork) or cannot be
  read,
- lacks one of the two indexes,
- has an index built 6 days 12 hours ago or earlier, or
- names another index schema than the one the workflow's commit builds,

and on a run started by hand with the `timetables` input ticked (rebuild
the Volán and city indexes now). 6 days 12 hours, not 7 days: GitHub starts
the scheduled runs hours late, by a different amount every day. With a full
week, a morning run that started a little earlier than the one a week before
would find the indexes a few minutes too young and leave them to the
afternoon run, and the rebuild would drift later week by week. With half a
day to spare, the same morning run of the next week rebuilds them.

The rebuild runs the release's builders (`scripts/build_volan_index.py`,
`scripts/build_city_index.py`) on the workflow's commit, with the release's
repository secrets and variables (`BAHART_GTFS_URL`,
`MOBILITY_DATABASE_REFRESH_TOKEN`, `TRANSITLAND_API_KEY`, `VOLAN_GTFS_URL`,
`HUNGARIAN_TRANSPORT_ALLOW_MISSING`; no MÁV login) and the release's guards
([When an update fails](#when-an-update-fails)). The previous indexes they
are compared with are the ones on the site (checked against
`timetables.json`, shapes included), or, when the site has none that check
out, the latest stable release's. A city feed whose download fails keeps
its part of that previous index (*City feed carried over*, see [When an
update fails](#when-an-update-fails)), so one publisher's outage does not
hold back the other feeds. Volán and city are independent: a builder that
fails, or whose guard refuses its index, leaves only that index as it is on
the site, with a warning on the run.

Every deploy replaces the whole site, so the run's `site` job puts it
together from both parts: the MÁV files of this run, else the ones already
published (every file `mav-index.json` names); each Volán or city index of
this run, else the published one with its shapes, checked against the
published `timetables.json` (the size and sha256 of every file). It deploys
when the MÁV part or at least one of the two indexes is new. When a file
the published manifests name cannot be carried over (it does not download,
or does not match), the run fails and deploys nothing: the new site would
lack that file. A manifest that is not there at all (404) carries nothing
and is no failure.

`timetables.json` (at most 64 KiB) describes both indexes (sizes, hashes
and counts here are examples):

```json
{
 "format": 1,
 "schema": 29,
 "built_at": "2026-11-16T10:40:00Z",
 "commit": "…",
 "indexes": {
  "volan": {
   "url": "volan-index.json.gz", "size": 3600000, "sha256": "…",
   "feed": "volan:01716.20261102", "built_at": "2026-11-16T10:40:00Z",
   "trips": 120000, "stops": 9000,
   "shapes": {"dir": "volan-shapes-0123456789", "n": 64,
              "files": {"00.json.gz": {"size": 90000, "sha256": "…"}, "…": {}}}
  },
  "city": {
   "url": "city-index.json.gz", "size": 1400000, "sha256": "…",
   "feed": "miskolc:1.3;veszprem:20260831-1;…", "built_at": "2026-11-09T10:35:00Z",
   "trips": 60000, "stops": 7000, "operators": 22,
   "shapes": {"dir": "city-shapes-…", "n": 64, "files": {"…": {}}}
  }
 }
}
```

- `format` is the manifest's (1); `schema` the indexes' format (`"v"` in
  each index, the same for both); `built_at` is when this site was put
  together, `commit` the commit of the run.
- Per index: `url`, `size` and `sha256` of the index file; `feed`, exactly
  the index's own `"feed"` (the Volán feed version, or the city operators'
  feed versions); `built_at`, when that index was built; `trips`, `stops`,
  and for the city index `operators`.
- `shapes` names the folder from the index's head (`dir`), its number of
  files (`n`, 64) and the size and sha256 of each, `00.json.gz` to
  `63.json.gz`; it is `null` for an index without shapes.
- An index carried over from the previous site keeps its entry as it was,
  `built_at` included, so the week counts from its last real build. An
  index that was never built is not listed.

The workflow builds the commit it runs on (main), not the latest release.
When main changes the index format before a release, the site carries the
new `schema` from its next rebuild, and installs of the older release skip
the Volán and city indexes there (`unsupported`) and keep theirs until they
update; a release with the new format brings indexes of it.

**Installing them.** The integration checks `timetables.json` in the same
round as the daily MÁV index, right after it: about 10 to 20 minutes after
Home Assistant starts (or the options are saved) and then once a day at the
*Daily check time*. A failed check is tried again a few times that day. A
manifest whose `format` or `schema` this version does not read is skipped
(one line in the log; nothing installed). Otherwise each index is installed
when it is missing from `www/` or its feed on the site is newer than the
one in use:

- Volán: the date in its feed version is later.
- City: compared operator by operator. No operator in both indexes may have
  an older dated feed on the site, and at least one has a later one, or the
  set of operators differs. A feed that cannot be dated counts as newer when
  it differs.

So an index the [local rebuild](#keeping-the-gtfs-indexes-current) made
from newer feeds stays, and so does a release's index whose feeds are newer
than the site's. After a HACS update the release's indexes are served until that
check runs, which installs the site's again when they are newer.

Nothing in `www/` changes until the download checks out: the index's size
and sha256, a single gzip stream, its head (`"v"` the manifest's `schema`,
`"feed"` its feed, `"sh"` its shapes), and the size and sha256 of every
shapes file; at most 48 MiB for an index, 8 MiB for a shapes file and
160 MiB for an index with its shapes. A file that does not match right after
a deploy is looked at again later, as for the MÁV index (the Pages cache,
above). The shapes folder goes in first, written under a temporary name and
renamed into place (a folder of that name that already checks out is kept),
then the index, as a temp file renamed over the old one, so the card never
reads an index whose shapes are missing. The previous shapes folder is
marked retired and removed 26 hours later: an open dashboard keeps the old
index until it reloads (at the latest at midnight) and reads its shapes
until then. Retired folders are looked at in every check and at every start
of the integration, also with *Weekly Volán and city timetables from
GitHub* off. The timetable sensors update at once, and the install is
recorded, so a later release that ships the same feeds or older ones does
not announce them as new timetables.

#### Options, privacy and notifications

**Opting out.** Settings → Devices & services → Hungarian transport →
Configure: *Daily MÁV route index from GitHub* for the MÁV index, *Weekly
Volán and city timetables from GitHub* for the other two; both are on by
default. Off, Home Assistant makes no request to GitHub Pages for those
indexes and uses the ones it has: the release's, one installed from Pages
before (until an update of the integration replaces it), or the ones it
builds from your feeds.

**Privacy.** With the MÁV option on, Home Assistant contacts
`bator.github.io` (GitHub Pages) once a day, and up to 4 more times that day
for the manifest alone while it is not yet of the day; with the timetables
option on, once a day for `timetables.json`, and for the indexes and their
shapes only when they are newer (about once a week). Each is a plain HTTPS
GET (never the GTFS zips), no cookies, nothing beyond what any HTTP request
carries (your IP address, and Home Assistant's user agent with its
version). Nothing about your configuration is sent.

**Notifications.** With *Notify about timetable updates* on in the same
options, the integration creates a persistent notification (one per kind; a
new one replaces the old) when:

- the daily MÁV index was installed (the feed's date, trains and lines).
  MÁV publishes almost daily, so this one calls the service below only with
  *Also notify about daily MÁV index installs* on;
- the Volán or city index from GitHub Pages was installed, naming which
  and their feeds, in the style of the one about shipped timetables below;
  not when the round only installs again the index it installed last (a
  HACS update put the release's older one back in the meantime);
- after an update of the integration, the indexes it ships (Volán, city,
  MÁV) are of other feeds than the ones it saw before, naming which. The
  first run only records them; changes the integration makes itself (the
  daily MÁV index, the Volán and city indexes from Pages, a local build)
  are not news, nor a shipped index whose feeds the integration already
  installed from Pages, nor a shipped MÁV index older than the daily one in
  use;
- the daily MÁV index failed on 3 consecutive days: once, and the notification
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
Transitous, and then to a line through its stops. City trams, trolleybuses,
the ferries, the Sikló (funicular) and the Libegő (aerial lift) keep their
GTFS vehicle type.

A feed that runs from `frequencies.txt` (the Sikló every 10 minutes, the
Dunakeszi ferry every 20) is expanded into one trip per start: from each
`start_time`, every `headway_secs`, while before `end_time`, as GTFS
specifies for both `exact_times` values. Service more frequent than every
5 minutes (the Libegő's chairs, every 20 seconds) is listed every 5 minutes:
the board shows that the lift runs and how long the ride takes, not every
chair.

## Keeping the GTFS indexes current

HACS installs the indexes that were built when the GitHub release was cut,
and a published release never changes afterwards. A release is made for
new code only, and ships the indexes current at release time: Volán, city,
and MÁV when built. Between releases the integration installs the newer
timetables itself from GitHub Pages: the Volán and city indexes rebuilt
every week, and the MÁV index every day ([The timetables on GitHub
Pages](#the-timetables-on-github-pages)). So new timetables need no new
version, and HACS offers an update only for new code. (Up to the 1.10
releases, a weekly job published new timetables as a patch release on the
latest stable code instead.)

Version numbers: a `rev` run publishes main as the prerelease
`X.Y.(Z+2)-rev.N` after stable `X.Y.Z` (1.9.15-rev.1 after 1.9.13), leaving
the next patch (1.9.14) free. The weekly timetable releases used to take
that number; the gap stays so that existing beta installs stay ordered: a
beta install on `X.Y.(Z+2)-rev.N` is only offered a higher version, and a
series that started on the next patch instead would rank below it. A later
`rev` run continues an open series, or starts one two patches above a newer
stable. A `patch` run publishes main under the open series' number
(1.9.15), so what the beta ran ships under its own version; without an open
series it is the next patch. The version is picked from GitHub's list of
releases and tags; when GitHub does not answer, the run stops instead of
guessing. A release run compares the new indexes with the latest published
ones: a city feed that is gone, a feed that lost more than half of its
trips, or a Volán index with far too few trips fails the run. Trips whose
own service has ended (a summer timetable in October) do not count as lost:
the comparison is on the trips that still run, and when those are under a
quarter of the old index, the new one has to keep 90% of them (under 50
still running: all trips). A failed run makes no release (see [When an
update fails](#when-an-update-fails)); a city feed whose download fails
fails a release too, where the weekly Pages rebuild carries it over.
To accept a feed that is gone for good, list its id in the `allow_missing`
input of a manual release run, or in the `HUNGARIAN_TRANSPORT_ALLOW_MISSING`
repository variable for every run, the weekly Pages rebuild included (the
input adds to the variable), and drop it from `CITY_FEEDS`.
The local rebuild below keeps an install current every day, ahead of the
weekly indexes on Pages.
Every release also publishes `SHA256SUMS`, `hungarian-transport-build.json`
and a build provenance attestation: check a download with
`sha256sum -c SHA256SUMS`
(next to the zips) or

```sh
gh release download v1.10.20 --repo bator/hungarian-transport --pattern hungarian-transport.zip
gh attestation verify hungarian-transport.zip --repo bator/hungarian-transport \
  --signer-workflow bator/hungarian-transport/.github/workflows/release.yml \
  --source-ref refs/heads/main
```

`--signer-workflow` pins the release workflow: another workflow of the
repository signs with the same repository, and its files do not pass.
`--source-ref` is the ref the run started from: `refs/heads/main` for a
release the workflow tagged itself, a manual run (`"event":
"workflow_dispatch"` in `hungarian-transport-build.json`; a manual run from
another branch stops before it builds anything), which is how releases are
made, and the weekly timetable releases up to 1.10 (`"event": "schedule"`).
Only a release cut by pushing its tag (`"event": "push"`) was built from
`refs/tags/<tag>`, and passes with that instead.
The attestation names the commit of the workflow run that built the zip;
`hungarian-transport-build.json` gives `built_commit` (the tag's) and
`workflow_commit` (the run's), the same commit. On a weekly timetable
release up to 1.10 they differ: the workflow ran on the head of `main` and
built the stable tag's commit.

To rebuild them yourself on Home Assistant OS:

1. Copy `scripts/*.py` and `scripts/run_update.sh` to `/config/hungarian-transport/`
   (`build_mav_index.py` included). The scripts read GTFS with the
   integration's `gtfs_feed.py` in `/config/custom_components/hungarian_transport/`,
   so copy the scripts of the installed release; without the integration
   installed, copy `custom_components/hungarian_transport/gtfs_feed.py` next
   to them. pfaedle output stays on the builder host,
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

## Timetable sensors

The integration has four sensors on one service device, *Hungarian
transport*, so a dashboard or an automation can tell which timetables the
cards use and when they changed:

| Entity | State | Attributes |
| --- | --- | --- |
| `sensor.hungarian_transport_mav_route_index` (MÁV route index) | date of the served MÁV index's feed (`20261030.M2ChangeID` → 2026-10-30) | `feed_version`; `source`: `daily` (the daily index installed it), `local` (a build of `/config/hungarian-transport/mav-gtfs.zip`, an upload, or the local updater, whose durable copy is this index) or `release`; `installed_at` (UTC); `trains`, `lines`; `fitted_share` (the share of routes that follow the rails rather than the line through the stations); `audit_ok_ratio` when the build or the manifest gave one |
| `sensor.hungarian_transport_volan_timetable` (Volán timetable) | date in the Volán index's feed version (`volan:01716.20261102` → 2026-11-02) | `feed_version`, `trips`, `stops`, `updated_at` (the index's build time from its gzip header, else the file's time), `source`: `pages` when it is the index the integration installed from [GitHub Pages](#the-volán-and-city-indexes), `local_updater` when the updater's copy in `/config/hungarian-transport` is this index, else `release`; `pages_checked_at`, `pages_result` (below) |
| `sensor.hungarian_transport_city_timetables` (City timetables) | when the city index was built (timestamp) | `operators`, `trips`, and `feeds` (operator → feed version) when there are at most 10; `source`: `pages`, `local_updater` or `release`, as for Volán; `pages_checked_at`, `pages_result` (below) |
| `sensor.hungarian_transport_daily_mav_check` (Daily MÁV check) | when the daily MÁV index last ran (timestamp; unknown before its first round) | `result`: `installed`, `up_to_date`, `superseded` (www/ serves a newer feed than the manifest's), `local_wins`, `busy`, `failed`, `unsupported`, or `disabled` with the option off; `manifest_feed`, `manifest_built_at`, `next_run` (UTC), `failure_streak` (days in a row) |

`pages_checked_at` is when the integration last read `timetables.json`
(UTC), and `pages_result` what that check did for this index: `installed`,
`up_to_date` (the site serves the feed in use), `superseded` (the index in
use has a newer feed than the site's), `local_wins` (the local updater's
copy in `/config/hungarian-transport` has feeds the site's do not beat; the
integration leaves it, so the two never take turns), `not_published` (the
manifest has no entry for this index), `failed` (the manifest or the download did not check out;
the index stays as it was), `unsupported` (a manifest format or index
schema this version does not read), or `disabled` with the *Weekly Volán
and city timetables from GitHub* option off.

An index sensor is unavailable while its index is missing from www/. The
sensors update right after the daily index, a Pages install or a local MÁV
build changed something, and every 30 minutes otherwise, which catches the
local updater and HACS. Each index is read again only when its file changed
(size or time), in the background: the Volán and city indexes are
streamed, not loaded.

An entities card for a dashboard:

```yaml
type: entities
title: Timetables
entities:
  - entity: sensor.hungarian_transport_mav_route_index
    secondary_info: last-changed
  - type: attribute
    entity: sensor.hungarian_transport_mav_route_index
    attribute: source
    name: MÁV index source
  - entity: sensor.hungarian_transport_volan_timetable
  - type: attribute
    entity: sensor.hungarian_transport_volan_timetable
    attribute: source
    name: Volán timetable source
  - entity: sensor.hungarian_transport_city_timetables
    format: relative
  - entity: sensor.hungarian_transport_daily_mav_check
    format: relative
  - type: attribute
    entity: sensor.hungarian_transport_daily_mav_check
    attribute: result
    name: Daily MÁV check result
```

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

The indexes from GitHub Pages fail on two sides:

- On GitHub, a Volán or city build that fails, or that a guard below
  refuses, leaves the published index on the site, with a warning on the
  run. That index keeps its old `built_at`, so the next run (twice a day)
  tries again. A published file that cannot be carried over into the new
  site fails the run, and nothing is deployed: the site stays as it was.
- In Home Assistant, a check that fails (GitHub unreachable, a redirect, a
  file that still does not match its manifest when looked at again) leaves
  `www/` as it was, logs a warning and shows `failed` in the sensors'
  `pages_result`; it is tried again later that day and at the next daily
  check. A manifest this version does not read (`unsupported`) installs
  nothing until the integration is updated; the indexes in use stay.

The city index (locally, in a release run and in the weekly Pages rebuild)
is not replaced when the rebuild would lose a feed that the index in place
still runs, or more than half of a feed's trips. Each feed is judged on its own. A feed whose trips
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
  On the weekly Pages rebuild such a feed keeps its timetable from the
  previous index (the site's, else the latest release's) instead, with a
  *City feed carried over* warning that names the error, as long as no more
  than three feeds failed at once (more is the network, not one publisher)
  and the previous index has a running timetable of it; a feed listed in
  `HUNGARIAN_TRANSPORT_ALLOW_MISSING` is left out as before. A release run
  carries nothing over: such a feed fails it, unless `allow_missing` names
  it.
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

Where the publisher has no fixed zip URL, or one behind a login, the city
index uses the public menetbrand `/download/<slug>` proxy (Debrecen DKV,
Kecskemét KeKo, Kaposvár, Hódmezővásárhely, Kiskunhalas, Székesfehérvár,
Tatabánya, Várpalota). The [menetbrand catalog](https://gtfs.menetbrand.com/letoltes/)
lists DKV as *Nem publikus* and KeKo as *Igénylés* (on request), and neither
feed states a licence; KeKo's own address
(<https://timetable.keko.hu/Kecskemet_GTFS.zip>) answers 401. Orosháza's
local buses come from the town's own export at gpspositions.net.

The Budavári Sikló, the Zugligeti Libegő, the Dunakeszi–Horány ferry, the
Pásztor ferry (Szentendre–Szigetmonostor) and the Üröm minibus come from
[Máté Gyöngyösi's feeds](https://github.com/gy-mate/gtfs-feeds) on
`gy-mate.hu/gtfs`, under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/):
credit them when you publish anything built on these files. BKK's own GTFS
(and so FUTÁR) has neither the Sikló nor the Libegő.

Not wired:

- Weekendbus (Csömör/Pécel) still hosts a zip (`apps.weekendbus.hu`, the same
  one menetbrand serves), but its calendar ended 2025-12-31 (`feed_version`
  20250119). Those lines now run as Volán / MÁV.
- GYSEV: its trains run in MÁV's GTFS (the MÁV index). The separate GYSEV
  zip (a mirror at jbb.ghsq.de, no shapes, no licence) adds only services in
  Austria (Wien–Deutschkreutz, Graz–Fehring, Graz–Szentgotthárd, whose only
  Hungarian station is Szentgotthárd) and rail replacement buses.

Same-town networks stay apart: a city feed and Volánbusz's local lines of
the same town (Székesfehérvár, Szeged) are separate operators in the picker,
with their own stop ids, and a card reads one operator, so no departure is
listed twice. Debrecen, Kecskemét and Orosháza have no Volánbusz local
lines in the Volán feed. Bahart prefers the
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

BKK static GTFS is public, but Budapest already uses live FUTÁR, so that zip
is not folded into `city-index.json.gz`. The BKK Open Data key is only for
FUTÁR / GTFS-RT.

## Card-only install and building the indexes

For a frontend-only manual install, unzip `hungarian-transport-frontend.zip`
from a GitHub release into `/config/www/hungarian-transport/` and add the
`/local/hungarian-transport/hungarian-transport.js` resource (`type: module`).
The zip holds the card bundle, the `leaflet/` folder the maps load, and the
GTFS indexes with their shapes folders. This does not install the ELVIRA
backend, and nothing installs the newer indexes from GitHub Pages either:
download them yourself (`timetables.json` names each file and its shapes
folder, see [The Volán and city indexes](#the-volán-and-city-indexes)) or
use the local rebuild.

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
