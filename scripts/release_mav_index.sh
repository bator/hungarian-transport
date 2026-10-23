#!/usr/bin/env bash
# Build a rail-matched mav-index.json.gz on a CI runner: MÁV GTFS (HTTP Basic
# auth), the Hungary OSM extract, pfaedle (pinned image), then the checkout's
# own scripts/build_mav_index.py with its audit. Used by release.yml (from the
# workflow's own commit, on whatever tag it builds) and by mav-index.yml.
#
# Usage: release_mav_index.sh WORK_DIR
# Run from the checkout to build with; it writes there:
#   mav-index.json.gz  only when every step and both guards passed
#   mav-build.json     build and audit statistics, timings (no credentials)
# Exit status non-zero: no index was written (one left from before stays).
#
# Environment:
#   MAV_GTFS_USER, MAV_GTFS_PASSWORD  required unless MAV_GTFS_ZIP is set;
#                     written to a netrc file (umask 077) for the feed's host
#                     only, never to a command line, a log or the work dir's
#                     other files
#   MAV_GTFS_URL      optional, https on *.mavcsoport.hu only
#   MAV_GTFS_ZIP      optional: a MÁV zip downloaded before (another run's
#                     WORK_DIR/gtfs/mav-gtfs.zip); used instead of a download
#                     (no login), checked the same way
#   OSM_PBF           the Hungary extract; downloaded (md5-checked) if missing
#   MAV_INDEX_OUT     optional, default mav-index.json.gz
#   MAV_MIN_FITTED    share of variants that must follow their shape (pfaedle
#                     output does; MÁV's own coarse shapes fit about 3%)
#   PFAEDLE_TIMEOUT   how long pfaedle may run (timeout(1) syntax), default
#                     30m; a run that takes longer fails like any other
#   DOWNLOAD_RETRY_DELAY  seconds between download attempts, default 15
set -euo pipefail

work="${1:?usage: release_mav_index.sh WORK_DIR}"
out="${MAV_INDEX_OUT:-mav-index.json.gz}"
url="${MAV_GTFS_URL:-https://www.mavcsoport.hu/gtfs/gtfsMavMenetrend.zip}"
gtfs_zip="${MAV_GTFS_ZIP:-}"
osm_url="https://download.geofabrik.de/europe/hungary-latest.osm.pbf"
min_fitted="${MAV_MIN_FITTED:-0.9}"
pfaedle_timeout="${PFAEDLE_TIMEOUT:-30m}"
retry_delay="${DOWNLOAD_RETRY_DELAY:-15}"
# ad-freiburg/pfaedle, pinned by digest (bump deliberately).
image="ghcr.io/ad-freiburg/pfaedle@sha256:af4fdc647699ea1ffe5150fd1f9d264c465c2876710ea749e8cb284f13abd20d"

if [ -z "$gtfs_zip" ] && { [ -z "${MAV_GTFS_USER:-}" ] || [ -z "${MAV_GTFS_PASSWORD:-}" ]; }; then
  echo "MAV_GTFS_USER and MAV_GTFS_PASSWORD are required (or MAV_GTFS_ZIP)" >&2
  exit 2
fi
if [ -n "$gtfs_zip" ] && [ ! -s "$gtfs_zip" ]; then
  echo "MAV_GTFS_ZIP is not a file" >&2
  exit 2
fi
if [[ ! "$pfaedle_timeout" =~ ^[0-9]+[smhd]?$ ]] || [[ ! "$retry_delay" =~ ^[0-9]+$ ]]; then
  echo "PFAEDLE_TIMEOUT must look like 30m, DOWNLOAD_RETRY_DELAY like 15" >&2
  exit 2
fi
if [ ! -f scripts/build_mav_index.py ] || [ ! -f custom_components/hungarian_transport/mav_index.py ]; then
  echo "this checkout has no MÁV index builder" >&2
  exit 2
fi

mkdir -p "$work"
work="$(cd "$work" && pwd -P)"
# Only now that $work is absolute: docker -v takes a relative source for a
# volume name.
osm="${OSM_PBF:-$work/hungary-latest.osm.pbf}"
case "$osm" in /*) ;; *) osm="$PWD/$osm" ;; esac
if [ -n "$gtfs_zip" ]; then
  gtfs_zip="$(realpath "$gtfs_zip")"
  case "$gtfs_zip" in
    "$work"/gtfs/* | "$work"/gtfs-out/*)
      echo "MAV_GTFS_ZIP lies in the work dir this run clears; use another WORK_DIR" >&2
      exit 2 ;;
  esac
fi

# Whatever a failed or interrupted run leaves must not outlive it: the netrc,
# a half-downloaded extract (actions/cache would keep it for the week) and a
# pfaedle container that outlived its time limit.
netrc=""
container="mav-pfaedle-$$-$RANDOM"
pfaedle_started=false
cleanup() {
  if [ -n "$netrc" ]; then rm -f "$netrc"; fi
  rm -f "$osm.part" "$osm.md5"
  if [ "$pfaedle_started" = true ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

rm -rf "$work/gtfs" "$work/gtfs-out"
mkdir -p "$work/gtfs" "$work/gtfs-out"
t0=$SECONDS

if [ -n "$gtfs_zip" ]; then
  echo "MÁV GTFS from MAV_GTFS_ZIP, not downloaded"
  cp -f "$gtfs_zip" "$work/gtfs/mav-gtfs.zip"
  # What the checks below read from a download.
  : > "$work/mav-headers.txt"
  echo "200 - local" > "$work/mav-curl.txt"
else
# The feed's host, from a URL that may only be https on mavcsoport.hu,
# without userinfo: the credentials go nowhere else.
host="$(python3 -I - "$url" <<'PY'
import sys, urllib.parse
u = urllib.parse.urlsplit(sys.argv[1])
host = (u.hostname or "").lower()
if u.scheme != "https" or u.username or u.password or u.port not in (None, 443) \
        or not (host == "mavcsoport.hu" or host.endswith(".mavcsoport.hu")):
    sys.exit("MAV_GTFS_URL must be https on mavcsoport.hu, without credentials")
print(host)
PY
)"

# netrc for that host only: curl sends it to this machine and to no host a
# redirect leads to. Written from the environment by Python, so the values
# are on no command line; umask 077 before the file exists.
netrc="$work/netrc"
(
  umask 077
  python3 -I - "$host" "$netrc" <<'PY'
import os, sys
host, path = sys.argv[1], sys.argv[2]
def tok(v: str) -> str:
    # Always quoted: curl (7.84+) reads a quoted netrc token with \\ and \"
    # escapes, and an unquoted one starting with # as a comment (an empty
    # password, then a refused login).
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
user, password = os.environ["MAV_GTFS_USER"], os.environ["MAV_GTFS_PASSWORD"]
if any(ord(c) < 32 for c in user + password):
    sys.exit("MAV_GTFS_USER / MAV_GTFS_PASSWORD hold a control character")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as fh:
    fh.write(f"machine {host} login {tok(user)} password {tok(password)}\n")
PY
)

# Retried here, not by curl: a refused login (401/403) is final, as every
# retry is another login with the same credentials (an account lockout).
# Other 4xx answers are final too, except 408 and 429; 5xx, timeouts and
# broken connections are tried 6 times.
echo "downloading the MÁV GTFS from $host"
attempts=6
attempt=1
while :; do
  rc=0
  curl --fail --silent --show-error --location --max-redirs 3 \
    --proto =https --proto-redir =https --netrc-file "$netrc" \
    --connect-timeout 30 --max-time 600 \
    --max-filesize 314572800 -A "hungarian-transport-release (+https://github.com/${GITHUB_REPOSITORY:-bator/hungarian-transport})" \
    --dump-header "$work/mav-headers.txt" -o "$work/gtfs/mav-gtfs.zip" \
    -w '%{http_code} %{size_download} %{content_type}\n' "$url" > "$work/mav-curl.txt" || rc=$?
  if [ "$rc" -eq 0 ]; then break; fi
  code="$(cut -d' ' -f1 "$work/mav-curl.txt" 2>/dev/null || true)"
  case "$code" in
    401 | 403)
      echo "MÁV GTFS: HTTP $code, the login was refused; not retried (check MAV_GTFS_USER / MAV_GTFS_PASSWORD)" >&2
      exit 1 ;;
    408 | 429) ;;
    4??)
      echo "MÁV GTFS: HTTP $code; not retried" >&2
      exit 1 ;;
  esac
  if [ "$rc" -eq 63 ]; then
    echo "MÁV GTFS: larger than --max-filesize; not retried" >&2
    exit 1
  fi
  if [ "$attempt" -ge "$attempts" ]; then
    echo "MÁV GTFS: download failed (curl exit $rc, HTTP ${code:-none}) $attempts times" >&2
    exit 1
  fi
  echo "MÁV GTFS: curl exit $rc, HTTP ${code:-none}; attempt $((attempt + 1)) of $attempts in ${retry_delay}s" >&2
  attempt=$((attempt + 1))
  sleep "$retry_delay"
done
rm -f "$netrc"
fi
t_dl=$((SECONDS - t0))

# The zip as served: the last response's Content-Length (when it sent one)
# equals what arrived, and it is a GTFS zip with the members the build needs.
python3 -I - "$work/gtfs/mav-gtfs.zip" "$work/mav-headers.txt" "$work/mav-curl.txt" <<'PY'
import sys, zipfile
path, headers, curl_w = sys.argv[1:4]
code, size, ctype = (open(curl_w).read().split() + ["", "", ""])[:3]
blocks = open(headers, encoding="latin-1").read().replace("\r", "").strip().split("\n\n")
length = None
for line in blocks[-1].splitlines():
    name, _, value = line.partition(":")
    if name.strip().lower() == "content-length":
        length = int(value.strip())
actual = len(open(path, "rb").read())
if code != "200":
    sys.exit(f"MÁV GTFS: HTTP {code}")
if length is not None and length != actual:
    sys.exit(f"MÁV GTFS: got {actual} of {length} bytes")
if actual < 1_000_000:
    sys.exit(f"MÁV GTFS: only {actual} bytes")
with zipfile.ZipFile(path) as z:
    bad = z.testzip()
    if bad:
        sys.exit(f"MÁV GTFS: {bad} is corrupt")
    names = {n.lower() for n in z.namelist()}
missing = [n for n in ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt") if n not in names]
if missing:
    sys.exit("MÁV GTFS lacks " + ", ".join(missing))
print(f"MÁV GTFS ok: HTTP {code}, {actual} bytes ({ctype}), Content-Length {length}, {len(names)} files")
PY
rm -f "$work/mav-headers.txt" "$work/mav-curl.txt"

t1=$SECONDS
if [ ! -s "$osm" ]; then
  echo "downloading $osm_url"
  mkdir -p "$(dirname "$osm")"
  curl --fail --silent --show-error --location --proto =https --proto-redir =https \
    --retry 5 --retry-delay "$retry_delay" --retry-all-errors --connect-timeout 30 --max-time 1200 \
    -o "$osm.part" "$osm_url"
  curl --fail --silent --show-error --location --proto =https --proto-redir =https \
    --retry 5 --retry-delay "$retry_delay" --retry-all-errors -o "$osm.md5" "$osm_url.md5"
  want="$(cut -d' ' -f1 "$osm.md5")"
  got="$(md5sum "$osm.part" | cut -d' ' -f1)"
  if [ -z "$want" ] || [ "$want" != "$got" ]; then
    echo "OSM extract md5 mismatch" >&2
    rm -f "$osm.part" "$osm.md5"
    exit 1
  fi
  mv "$osm.part" "$osm"
  rm -f "$osm.md5"
fi
echo "OSM extract: $(du -h "$osm" | cut -f1)"
t_osm=$((SECONDS - t1))

t2=$SECONDS
docker pull -q "$image" >/dev/null
# No network inside: pfaedle reads the two files and writes the feed. About
# a minute; one that hangs is stopped at PFAEDLE_TIMEOUT (the container is
# removed on exit) and the run fails as on any pfaedle error.
pfaedle_started=true
rc=0
timeout --kill-after=60 "$pfaedle_timeout" \
  docker run --rm --name "$container" --network none \
  -v "$osm":/osm/hungary-latest.osm.pbf:ro \
  -v "$work/gtfs":/gtfs:ro \
  -v "$work/gtfs-out":/gtfs-out \
  "$image" -D -m rail -x /osm/hungary-latest.osm.pbf -i /gtfs/mav-gtfs.zip -o /gtfs-out || rc=$?
if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
  echo "pfaedle did not finish within $pfaedle_timeout" >&2
  exit 1
elif [ "$rc" -ne 0 ]; then
  echo "pfaedle failed (exit $rc)" >&2
  exit 1
fi
t_pfaedle=$((SECONDS - t2))

# pfaedle writes the tables it read and matched; any other member of MÁV's
# zip (feed_info.txt with the feed version, ...) is taken from it. Never its
# shapes.txt: the routes must be pfaedle's.
python3 -I - "$work/gtfs/mav-gtfs.zip" "$work/gtfs-out" <<'PY'
import shutil, sys, zipfile
from pathlib import Path
src, out = sys.argv[1], Path(sys.argv[2])
have = {p.name.lower() for p in out.iterdir() if p.is_file()}
need = ("routes.txt", "trips.txt", "stops.txt", "stop_times.txt", "shapes.txt")
missing = [n for n in need if n not in have]
if missing:
    sys.exit("pfaedle output lacks " + ", ".join(missing))
added = []
with zipfile.ZipFile(src) as z:
    for info in z.infolist():
        name = Path(info.filename).name
        if info.is_dir() or not name.lower().endswith(".txt") or name.lower() in have or name.lower() == "shapes.txt":
            continue
        with z.open(info) as fh, open(out / name, "wb") as dst:
            shutil.copyfileobj(fh, dst)
        added.append(name)
print("pfaedle output:", ", ".join(sorted(p.name for p in out.iterdir() if p.is_file())))
if added:
    print("taken from the MÁV zip:", ", ".join(sorted(added)))
PY

t3=$SECONDS
# The checkout's own builder and audit (the index schema its card reads).
# Written next to the work dir first: the coarse-shape guard decides. No
# bytecode: the builder imports the integration's package, which is zipped.
python3 -B - "$work/gtfs-out" "$work/mav-index.json.gz" "$work/mav-build.json" "$min_fitted" <<'PY'
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from build_mav_index import build_mav_index
feed, out, report, min_fitted = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])
out.unlink(missing_ok=True)
stats = build_mav_index(feed, out)
variants = stats.get("variants") or 0
fitted = stats.get("fitted") or 0
stats["fitted_share"] = round(fitted / variants, 4) if variants else 0.0
report.write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
if not variants or fitted / variants < min_fitted:
    out.unlink(missing_ok=True)
    sys.exit(f"only {fitted}/{variants} variants follow their shape (< {min_fitted:.0%}): "
             "not a rail-matched feed, not shipped")
PY
t_build=$((SECONDS - t3))

python3 -I - "$work/mav-build.json" "$t_dl" "$t_osm" "$t_pfaedle" "$t_build" <<'PY'
import json, sys
path = sys.argv[1]
stats = json.load(open(path, encoding="utf-8"))
stats["seconds"] = dict(zip(("download", "osm", "pfaedle", "build_audit"), map(int, sys.argv[2:6])))
json.dump(stats, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
PY
cp -f "$work/mav-index.json.gz" "$out"
cp -f "$work/mav-build.json" mav-build.json

python3 -I - mav-build.json "$out" <<'PY' | tee -a "${GITHUB_STEP_SUMMARY:-/dev/null}"
import json, os, sys
s = json.load(open(sys.argv[1], encoding="utf-8"))
a = s.get("audit") or {}
t = s.get("seconds") or {}
print("### MÁV index (pfaedle)")
print()
print("| | |")
print("| --- | --- |")
print(f"| feed | `{s.get('feed_version')}` |")
print(f"| trains / lines | {s.get('trains')} / {s.get('lines')} |")
print(f"| variants following their shape | {s.get('fitted')}/{s.get('variants')} ({s.get('fitted_share', 0):.1%}) |")
print(f"| audit | {a.get('trains_ok')}/{a.get('trains')} trains, {a.get('trips', 0) - a.get('trips_bad', 0)}/{a.get('trips')} trips on route ({a.get('ok_ratio', 0):.2%}) |")
print(f"| size | {os.path.getsize(sys.argv[2])} bytes |")
print(f"| seconds | download {t.get('download')}, OSM {t.get('osm')}, pfaedle {t.get('pfaedle')}, build+audit {t.get('build_audit')} |")
PY
