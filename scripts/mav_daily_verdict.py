#!/usr/bin/env python3
"""Whether a daily MÁV run that published nothing goes red (mav-index-daily.yml).

A run that deploys nothing keeps the previous site on GitHub Pages, and the
integration keeps its index (mav_daily.py says UP_TO_DATE). That is fine for
a day; it must not go on unnoticed. The run fails (exit 1, ::error::):

- at once when MÁV refused the login (HTTP 401/403; release_mav_index.sh
  exits AUTH_EXIT and never retries it, as every retry is another login):
  only new credentials help;
- when nothing was built or published (FAILURES) and the published
  manifest's built_at is STALE_AFTER or older, or names no build time;
- when nothing was built or published and the published manifest is not
  trusted: its mav-index.json.sig is not there (HTTP 404) or does not
  verify against the pinned public key (sign_manifest.py), also when both
  are read again once, --sig-settle seconds later (Pages' CDN may serve one
  deploy's manifest next to the other's .sig, or a 404 it cached for the
  .sig before the first signed deploy, for up to PAGES_MAX_AGE:
  mav_pages_site.SIG_SETTLE_S); and when the .sig is there but is no
  signature (larger than MAX_SIG) or the site answers it with another
  client error (HTTP 4xx). Its built_at is then anyone's: one who can put
  files on Pages could keep a failing build green for good with a fresh
  one, and installs take nothing from it anyway (mav_daily.py).

Otherwise it stays green with a ::warning:: naming the published feed's age,
also when the published manifest or its signature cannot be read for a
network error, a timeout or a server error (HTTP 5xx: its age is unknown,
and an outage must not turn the run red). Nothing here deploys or removes
anything: the workflow deploys only a site that was made.

Usage: mav_daily_verdict.py --failure auth|build|site|secrets
         (--live-url URL | --live-file PATH) [--now ISO8601]
         [--sig-settle SECONDS]
--live-file reads the signature from the file next to it (PATH.sig).
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import sign_manifest  # noqa: E402
from mav_pages_site import MANIFEST_NAME, MAX_SIG, SIG_SETTLE_S, built_time, fetch_bytes, parse_live  # noqa: E402

# release_mav_index.sh's exit status for a refused login (401/403).
AUTH_EXIT = 77
STALE_AFTER = timedelta(days=2)
FAILURES = {
    "auth": "MÁV refused the login (HTTP 401/403); check MAV_GTFS_USER / MAV_GTFS_PASSWORD",
    "build": "the MÁV download, pfaedle or the audit failed",
    "site": "the built index did not make a Pages site",
    "secrets": "there are no MAV_GTFS_USER / MAV_GTFS_PASSWORD secrets",
}


# live_state's trust: the signature verifies; it is not there, does not
# verify or is no signature; it could not be read (a network error, a
# timeout, HTTP 5xx).
TRUSTED, UNTRUSTED, UNKNOWN = "ok", "untrusted: ", "unknown: "


def verdict(failure: str, live: dict | None, why: str, now: datetime,
            trust: str = TRUSTED) -> tuple[bool, str]:
    """(fail the run, the annotation to print) for a run that published nothing
    because of failure (a FAILURES key), with the published manifest live (None
    when it could not be read, why says why) at now. trust (live_state): its
    built_at counts only when its signature verified."""
    if failure not in FAILURES:
        raise ValueError(f"unknown failure {failure!r}")
    cause = FAILURES[failure]
    if failure == "auth":
        return True, (f"::error title=Daily MÁV index: login refused::{cause[0].upper()}{cause[1:]}. "
                      "Not retried (an account lockout); GitHub Pages keeps the previous index.")
    if live is None:
        return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. The published "
                       f"manifest could not be read ({why}), so its age is unknown; GitHub Pages keeps what it has.")
    # The reason is this script's own text: nothing of an untrusted manifest
    # goes into the annotation.
    if trust.startswith(UNKNOWN):
        return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. The published "
                       f"manifest's signature could not be read ({trust.removeprefix(UNKNOWN)}), so its age is "
                       "unknown; GitHub Pages keeps what it has.")
    if trust != TRUSTED:
        return True, (f"::error title=Daily MÁV index: published manifest not trusted::Nothing published: {cause}, "
                      f"and the published {MANIFEST_NAME} is not trusted ({trust.removeprefix(UNTRUSTED)}): installs "
                      "take nothing from it, whatever its built_at says.")
    built = built_time(live)
    feed = live.get("feed")
    if built is None:
        return True, (f"::error title=Daily MÁV index: published manifest has no build time::Nothing published: "
                      f"{cause}, and the published manifest (feed {feed!r}) names no valid built_at.")
    age = now - built
    hours = int(age.total_seconds() // 3600)
    if age >= STALE_AFTER:
        return True, (f"::error title=Daily MÁV index is stale::Nothing published: {cause}. GitHub Pages still "
                      f"serves feed {feed!r}, built {live.get('built_at')} ({hours} h ago, the limit is "
                      f"{int(STALE_AFTER.total_seconds() // 3600)} h).")
    return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. GitHub Pages keeps "
                   f"feed {feed!r}, built {live.get('built_at')} ({hours} h ago).")


def _read(args: argparse.Namespace) -> tuple[bytes | None, str, bytes | None, str]:
    """The published manifest's bytes and its .sig's, each with why not."""
    if args.live_file is not None:
        body, why = (args.live_file.read_bytes(), "ok") if args.live_file.is_file() else (None, "no file")
        sig_file = args.live_file.with_name(args.live_file.name + ".sig")
        # A file that is not there stands for the site's 404, one over
        # MAX_SIG for what fetch_bytes says of such a .sig.
        if not sig_file.is_file():
            sig, sig_why = None, "HTTP 404"
        elif sig_file.stat().st_size > MAX_SIG:
            sig, sig_why = None, f"larger than {MAX_SIG} bytes"
        else:
            sig, sig_why = sig_file.read_bytes(), "ok"
        return body, why, sig, sig_why
    body, why = fetch_bytes(args.live_url)
    sig, sig_why = fetch_bytes(args.live_url + ".sig", MAX_SIG) if body is not None else (None, "not read")
    return body, why, sig, sig_why


_HTTP_RE = re.compile(r"HTTP (\d{3})")


def sig_unreadable(sig_why: str) -> str:
    """For a .sig that did not read (fetch_bytes' why, never "HTTP 404"):
    UNKNOWN when nothing can be told of it (a network error, a timeout, a
    server error, HTTP 5xx), else UNTRUSTED. A .sig that is there but is no
    signature (over MAX_SIG) or that the site refuses with another status
    (HTTP 403, 410, a redirect it did not follow) is what one who can put
    files on Pages can make it: counted as unknown, it would keep a failing
    build green whatever the manifest's age, so it counts as no signature
    (pages_timetables.py refuses the same .sig)."""
    status = _HTTP_RE.fullmatch(sig_why)
    if status is not None:
        return (UNKNOWN if status.group(1).startswith("5") else UNTRUSTED) + f"{MANIFEST_NAME}.sig: {sig_why}"
    if sig_why.startswith("larger than"):
        return f"{UNTRUSTED}{MANIFEST_NAME}.sig is no signature ({sig_why})"
    return f"{UNKNOWN}{MANIFEST_NAME}.sig: {sig_why}"


def live_state(args: argparse.Namespace, settle: float) -> tuple[dict | None, str, str]:
    """(the published manifest or None, why not, trust): trust TRUSTED when
    its .sig verifies over its exact bytes, UNTRUSTED... when the .sig is
    not there (HTTP 404) or does not verify, also when both are read again
    once settle seconds later (negative: never), and when it is no
    signature or the site refuses it (sig_unreadable), UNKNOWN... when the
    .sig could not be read (a network error, a timeout, HTTP 5xx). A 404
    next to a manifest that is there is read again as well: Pages' CDN may
    still answer the 404 it cached for the .sig before the first signed
    deploy (PAGES_MAX_AGE)."""
    for attempt in (1, 2):
        body, why, sig, sig_why = _read(args)
        live, why = parse_live(body) if body is not None else (None, why)
        if live is None:
            return None, why, UNKNOWN + "no manifest"
        if sig is None and sig_why != "HTTP 404":
            return live, why, sig_unreadable(sig_why)
        if sig is None:
            state = f"{UNTRUSTED}no {MANIFEST_NAME}.sig (HTTP 404)"
        elif sign_manifest.verify_bytes(body, sign_manifest.decode_sig(sig)):
            return live, why, TRUSTED
        else:
            state = f"{UNTRUSTED}{MANIFEST_NAME}.sig does not verify against the pinned public key"
        if attempt == 2 or settle < 0:
            return live, why, state
        print(f"{state.removeprefix(UNTRUSTED)}; reading both again in {settle:g} s (GitHub Pages may still serve"
              " the previous deploy's manifest or signature)")
        time.sleep(settle)
    raise AssertionError("unreachable")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--failure", required=True, choices=sorted(FAILURES))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--live-url")
    src.add_argument("--live-file", type=Path)
    ap.add_argument("--now", help="the time to measure the age at (ISO 8601 with offset); default now")
    ap.add_argument("--sig-settle", type=float, default=SIG_SETTLE_S,
                    help="seconds before a manifest whose signature does not verify, or is not there, is read"
                    " again with it"
                    f" (default {SIG_SETTLE_S:g}: Pages' cache age and a minute; negative: never)")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            ap.error("--now needs a UTC offset")
    if args.failure == "auth":
        live, why, trust = None, "not read", UNKNOWN + "not read"
    else:
        live, why, trust = live_state(args, args.sig_settle)
    fail, line = verdict(args.failure, live, why, now, trust)
    print(line)
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
