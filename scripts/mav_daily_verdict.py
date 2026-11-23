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
  one, and installs take nothing from it anyway (mav_daily.py);
- when nothing was built or published and the published manifest itself
  is not there (HTTP 404, also read again once, --sig-settle seconds
  later: a 404 Pages' CDN cached before the first deploy with the MÁV
  files), the site answers it with another client error (HTTP 4xx), or it
  is larger than mav_pages_site.MAX_MANIFEST or not a JSON object: the
  same reasoning, and every install's daily MÁV check fails on it. Not a
  404 when the repository has no MÁV secrets (failure secrets): such a
  repository publishes no MÁV files at all (its 404 is not read again
  either: it is a warning whatever a second read finds).

Otherwise it stays green with a ::warning:: naming the published feed's age,
also when the published manifest or its signature cannot be read for a
network error, a timeout or a server error (HTTP 5xx: its age is unknown,
and an outage must not turn the run red). Nothing here deploys or removes
anything: the workflow deploys only a site that was made.

--github-output FILE appends live_built=: the published manifest's
built_at when its signature verifies, else empty (also for a refused
login, read then without a second read). The site job's assemble takes it
as --mav-built-at, as it takes mav_pages_site.py's on a run that built:
it never carries an older mav-index.json over the one read here.

Usage: mav_daily_verdict.py --failure auth|build|site|secrets
         (--live-url URL | --live-file PATH) [--now ISO8601]
         [--sig-settle SECONDS] [--github-output FILE]
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
from mav_pages_site import MANIFEST_NAME, MAX_MANIFEST, MAX_SIG, SIG_SETTLE_S, built_time, fetch_bytes, parse_live  # noqa: E402

# release_mav_index.sh's exit status for a refused login (401/403).
AUTH_EXIT = 77
STALE_AFTER = timedelta(days=2)
FAILURES = {
    "auth": "MÁV refused the login (HTTP 401/403); check MAV_GTFS_USER / MAV_GTFS_PASSWORD",
    "build": "the MÁV download, pfaedle or the audit failed",
    "site": "the built index did not make a Pages site",
    "secrets": "there are no MAV_GTFS_USER / MAV_GTFS_PASSWORD secrets",
}


# live_state's trust: the signature verifies; it (or the manifest) is not
# there, does not verify or is no signature; it (or the manifest) could not
# be read (a network error, a timeout, HTTP 5xx).
TRUSTED, UNTRUSTED, UNKNOWN = "ok", "untrusted: ", "unknown: "
# The manifest itself answered with HTTP 404 (UNTRUSTED, but a warning for
# a repository without the MÁV secrets).
NO_MANIFEST = f"{UNTRUSTED}no {MANIFEST_NAME} (HTTP 404)"


def verdict(failure: str, live: dict | None, why: str, now: datetime,
            trust: str = TRUSTED) -> tuple[bool, str]:
    """(fail the run, the annotation to print) for a run that published nothing
    because of failure (a FAILURES key), with the published manifest live (None
    when it could not be read, why says why) at now. trust (live_state): its
    built_at counts only when its signature verified; with live None, how
    the manifest itself failed (manifest_unreadable: only UNKNOWN, and
    NO_MANIFEST for failure secrets, stay green)."""
    if failure not in FAILURES:
        raise ValueError(f"unknown failure {failure!r}")
    cause = FAILURES[failure]
    if failure == "auth":
        return True, (f"::error title=Daily MÁV index: login refused::{cause[0].upper()}{cause[1:]}. "
                      "Not retried (an account lockout); GitHub Pages keeps the previous index.")
    if live is None:
        # trust as live_state classes the manifest's own failure; by why
        # for a caller that did not.
        trust = manifest_unreadable(why) if trust == TRUSTED else trust
        if trust.startswith(UNKNOWN) or (trust == NO_MANIFEST and failure == "secrets"):
            return False, (f"::warning title=Daily MÁV index not published::Nothing published: {cause}. The "
                           f"published manifest could not be read ({why}), so its age is unknown; GitHub Pages "
                           "keeps what it has.")
        # This script's own text (why is fetch_bytes' or parse_live's).
        return True, (f"::error title=Daily MÁV index: published manifest unreadable::Nothing published: {cause}, "
                      f"and the published {MANIFEST_NAME} is not there or not a manifest ({why}): every install's "
                      "daily MÁV check fails on it.")
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
        # A file that is not there stands for the site's 404, one over
        # MAX_MANIFEST or MAX_SIG for what fetch_bytes says of it.
        if not args.live_file.is_file():
            body, why = None, "HTTP 404"
        elif args.live_file.stat().st_size > MAX_MANIFEST:
            body, why = None, f"larger than {MAX_MANIFEST} bytes"
        else:
            body, why = args.live_file.read_bytes(), "ok"
        sig_file = args.live_file.with_name(args.live_file.name + ".sig")
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


def manifest_unreadable(why: str) -> str:
    """For a manifest that did not read (fetch_bytes' why) or is no manifest
    (parse_live's): as sig_unreadable, UNKNOWN only for a network error, a
    timeout or HTTP 5xx; NO_MANIFEST for HTTP 404; UNTRUSTED for another
    status, one larger than MAX_MANIFEST, and one that is not a JSON object."""
    status = _HTTP_RE.fullmatch(why)
    if status is not None:
        if status.group(1) == "404":
            return NO_MANIFEST
        return (UNKNOWN if status.group(1).startswith("5") else UNTRUSTED) + f"{MANIFEST_NAME}: {why}"
    if why.startswith(("larger than", "not JSON", "not a JSON object")):
        return f"{UNTRUSTED}{MANIFEST_NAME} is no manifest ({why})"
    return f"{UNKNOWN}{MANIFEST_NAME}: {why}"


def live_state(args: argparse.Namespace, settle: float, failure: str = "") -> tuple[dict | None, str, str]:
    """(the published manifest or None, why not, trust): trust TRUSTED when
    its .sig verifies over its exact bytes, UNTRUSTED... when the .sig is
    not there (HTTP 404) or does not verify, also when both are read again
    once settle seconds later (negative: never), and when it is no
    signature or the site refuses it (sig_unreadable), UNKNOWN... when the
    .sig could not be read (a network error, a timeout, HTTP 5xx). A 404
    next to a manifest that is there is read again as well: Pages' CDN may
    still answer the 404 it cached for the .sig before the first signed
    deploy (PAGES_MAX_AGE). The manifest itself the same way
    (manifest_unreadable, None with it): a 404 read again once, UNKNOWN
    only when it could not be read. Not read again for failure secrets: a
    repository without the MÁV secrets publishes no MÁV files, its 404 is
    what it should be, and verdict keeps it green whatever a second read
    finds (waiting it out would only hold the run up)."""
    for attempt in (1, 2):
        body, why, sig, sig_why = _read(args)
        live, why = parse_live(body) if body is not None else (None, why)
        if live is None:
            state = manifest_unreadable(why)
            if state != NO_MANIFEST or failure == "secrets":
                return None, why, state
        elif sig is None and sig_why != "HTTP 404":
            return live, why, sig_unreadable(sig_why)
        elif sig is None:
            state = f"{UNTRUSTED}no {MANIFEST_NAME}.sig (HTTP 404)"
        elif sign_manifest.verify_bytes(body, sign_manifest.decode_sig(sig)):
            return live, why, TRUSTED
        else:
            state = f"{UNTRUSTED}{MANIFEST_NAME}.sig does not verify against the pinned public key"
        if attempt == 2 or settle < 0:
            return live, why, state
        if state == NO_MANIFEST:
            print(f"{state.removeprefix(UNTRUSTED)}; reading it again in {settle:g} s (GitHub Pages may still"
                  " serve a 404 it cached before the first deploy with the MÁV files)")
        else:
            print(f"{state.removeprefix(UNTRUSTED)}; reading both again in {settle:g} s (GitHub Pages may still"
                  " serve the previous deploy's manifest or signature)")
        time.sleep(settle)
    raise AssertionError("unreachable")


def live_built(live: dict | None, trust: str) -> str:
    """live_built= for --github-output: the published manifest's built_at
    (UTC, ISO), only when its signature verified; "" otherwise."""
    built = built_time(live) if live is not None and trust == TRUSTED else None
    return built.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") \
        if built is not None else ""


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
    ap.add_argument("--github-output", type=Path, help="append live_built= (the verified published built_at) here")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            ap.error("--now needs a UTC offset")
    if args.failure == "auth":
        # Red whatever is published; read only for live_built, once.
        live, why, trust = live_state(args, -1) if args.github_output is not None else (None, "not read", "")
        fail, line = verdict(args.failure, None, "not read", now, UNKNOWN + "not read")
    else:
        live, why, trust = live_state(args, args.sig_settle, args.failure)
        fail, line = verdict(args.failure, live, why, now, trust)
    print(line)
    if args.github_output is not None:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"live_built={live_built(live, trust)}\n")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
