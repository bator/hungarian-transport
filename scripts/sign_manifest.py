#!/usr/bin/env python3
"""Sign timetables.json and mav-index.json on GitHub Pages
(mav-index-daily.yml's sign job).

The integration installs the Volán and city indexes the GitHub Pages
manifest timetables.json names, each file checked against the manifest's
sha256 (custom_components/hungarian_transport/weekly_timetables.py), and
the daily MÁV index mav-index.json names (mav_daily.py). Both manifests are
signed: NAME.sig next to each is one line, the base64 of the 64-byte
ed25519 signature over NAME's exact bytes as deployed, and the integration
checks it against the public key it pins (PUBLIC_KEY here) before it reads
the manifest. Whoever could put files on
the Pages site, or answer for it, cannot make installs take an index
without the private key, which only the sign job's signing step sees (the
secret PAGES_SIGNING_KEY of the github-pages environment, which only main's
runs reach: base64 of the raw 32-byte ed25519 private key, its seed).

  sign   --site DIR
         Writes DIR/timetables.json.sig, and DIR/mav-index.json.sig when
         DIR has a mav-index.json (a site without the MÁV files has none).
         The key comes from the environment (PAGES_SIGNING_KEY), never the
         command line, and is never printed. Before it writes, the key's
         public half must be PUBLIC_KEY and each signature must verify
         against PUBLIC_KEY. Exit 1, with no .sig in DIR, for no key, a key
         that does not decode to 32 bytes, another key than the pinned
         one's, a signature that does not verify, no timetables.json, or no
         usable openssl: no unsigned manifest is ever deployed (the
         workflow uploads the site only after this).
  verify --site DIR
         Exit 0 when DIR/timetables.json.sig is a signature over
         DIR/timetables.json by PUBLIC_KEY's key, and so is
         DIR/mav-index.json.sig over DIR/mav-index.json when DIR has one;
         1 otherwise.
  live-check --site DIR --live-url URL
         Exit 1 (::error::) when GitHub Pages (URL is the site's https
         base URL) already serves files built later than DIR's: DIR is then
         an older run's site (a "Re-run failed jobs" of the sign job, whose
         artifact is kept 3 days, or of the site job, which assembles again
         from its run's MÁV and timetables parts, kept as long), and
         deploying it would put older indexes back over a newer run's.
         Each published manifest counts only when its .sig verifies
         against PUBLIC_KEY, and it is newer when
         - timetables.json names a later built_at than DIR/timetables.json
           (every deploy writes a new one, so it orders the deploys of the
           sign job's re-runs);
         - an index timetables.json lists (volan, city) names a later
           built_at than DIR/timetables.json's entry of that name: a site
           job re-run writes a timetables.json of the re-run's time around
           its run's older parts;
         - mav-index.json names a later built_at than DIR/mav-index.json,
           or DIR has none (the same re-run's MÁV part).
         A file carried over from the live site has the built_at it has
         there, and passes. Exit 0 otherwise, also when a published
         manifest cannot be read or is not trusted (its built_at is then
         anyone's: it must not hold a deploy back), and exit 1 for a
         DIR/timetables.json without a built_at. Run before the signing
         step, without the key.

pages_timetables.py verifies the published manifests with it before it
carries anything over from them (verify_bytes), and mav_pages_site.py the
published mav-index.json before it takes the day's index for one Pages
already serves: a site job never signs again what it did not sign itself.

The ed25519 work is openssl's (3.0 or later, for pkeyutl -rawin; GitHub's
Ubuntu runners have it): nothing is installed into the job that holds the
key. The private key reaches openssl on its stdin (PKCS#8 DER), never a
file or an argument, and openssl runs without PAGES_SIGNING_KEY in its
environment (_openssl).
"""
from __future__ import annotations

import argparse
import base64
import binascii
import http.client
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

MANIFEST_NAME = "timetables.json"
SIG_NAME = MANIFEST_NAME + ".sig"
# The daily MÁV index's manifest (mav_pages_site.MANIFEST_NAME), signed the
# same way when the site has it.
MAV_MANIFEST_NAME = "mav-index.json"
MAV_SIG_NAME = MAV_MANIFEST_NAME + ".sig"
# Every manifest the sign job signs, the first one always there.
MANIFESTS = (MANIFEST_NAME, MAV_MANIFEST_NAME)
KEY_ENV = "PAGES_SIGNING_KEY"
# The public key of PAGES_SIGNING_KEY: base64 of the raw 32 bytes. The
# integration pins the same key; a new key pair changes both.
PAGES_SIGNING_PUBLIC_KEY = "iUfHMDrTTf+3dV7JUu8ktyihkUfpkeTQRQxym955kq8="
PUBLIC_KEY = base64.b64decode(PAGES_SIGNING_PUBLIC_KEY)
# The DER around a raw ed25519 key (RFC 8410): a PKCS#8 private key of a
# 32-byte seed, a SubjectPublicKeyInfo of a 32-byte public key.
_PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
SIG_BYTES = 64
OPENSSL_TIMEOUT = 60
# live-check's reads of the published manifest (mav_pages_site.py's limits).
LIVE_TIMEOUT = 20
MAX_MANIFEST = 64 * 1024
MAX_SIG = 1024


class SignError(Exception):
    """The manifest cannot be signed. The text never holds the key."""


def _openssl(args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess:
    """openssl with args, stdin on its stdin. Its environment is this
    process's without KEY_ENV: the key reaches it on stdin only, and an
    environment is readable to more than its stdin is (/proc/PID/environ
    to the same user, a core dump, an engine or provider openssl loads)."""
    env = {k: v for k, v in os.environ.items() if k != KEY_ENV}
    try:
        return subprocess.run(["openssl", *args], input=stdin, capture_output=True, timeout=OPENSSL_TIMEOUT,
                              check=False, env=env)
    except (OSError, subprocess.SubprocessError) as err:
        raise SignError(f"openssl did not run: {type(err).__name__}") from None


def _first_line(raw: bytes) -> str:
    lines = raw.decode("utf-8", "replace").strip().splitlines()
    return lines[0][:200] if lines else "no message"


def key_from_env(environ: Mapping[str, str] | None = None) -> bytes:
    """The 32-byte seed in KEY_ENV (of environ, default os.environ)."""
    value = "".join(((os.environ if environ is None else environ).get(KEY_ENV) or "").split())
    if not value:
        raise SignError(f"{KEY_ENV} is not set")
    try:
        seed = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SignError(f"{KEY_ENV} is not base64") from None
    if len(seed) != 32:
        raise SignError(f"{KEY_ENV} decodes to {len(seed)} bytes, not the 32 of an ed25519 private key")
    return seed


def public_key(seed: bytes) -> bytes:
    """The raw 32-byte public key of a seed."""
    done = _openssl(["pkey", "-inform", "DER", "-pubout", "-outform", "DER"], _PKCS8_PREFIX + seed)
    if done.returncode != 0 or len(done.stdout) != len(_SPKI_PREFIX) + 32 or not done.stdout.startswith(_SPKI_PREFIX):
        raise SignError(f"openssl did not derive the public key: {_first_line(done.stderr)}")
    return done.stdout[len(_SPKI_PREFIX):]


def sign(data: Path, seed: bytes) -> bytes:
    """The 64-byte ed25519 signature over the bytes of the file data."""
    done = _openssl(["pkeyutl", "-sign", "-rawin", "-keyform", "DER", "-inkey", "/dev/stdin", "-in", str(data)],
                    _PKCS8_PREFIX + seed)
    if done.returncode != 0 or len(done.stdout) != SIG_BYTES:
        raise SignError(f"openssl did not sign: {_first_line(done.stderr)}")
    return done.stdout


def verify(data: Path, signature: bytes, public: bytes | None = None) -> bool:
    """Whether signature is public's key's (default PUBLIC_KEY's) ed25519
    signature over data."""
    public = PUBLIC_KEY if public is None else public
    if len(signature) != SIG_BYTES or len(public) != 32:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        pub, sig = Path(tmp) / "pub.der", Path(tmp) / "sig.bin"
        pub.write_bytes(_SPKI_PREFIX + public)
        sig.write_bytes(signature)
        done = _openssl(["pkeyutl", "-verify", "-rawin", "-pubin", "-keyform", "DER", "-inkey", str(pub),
                         "-in", str(data), "-sigfile", str(sig)])
    return done.returncode == 0


def read_sig(path: Path) -> bytes:
    """The signature in a .sig file (one line of base64); b"" when it does
    not read."""
    try:
        return decode_sig(path.read_bytes())
    except OSError:
        return b""


def verify_bytes(data: bytes, signature: bytes, public: bytes | None = None) -> bool:
    """verify, over bytes in memory (a manifest as fetched)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / MANIFEST_NAME
        path.write_bytes(data)
        return verify(path, signature, public)


def decode_sig(raw: bytes) -> bytes:
    """The signature in a .sig file's bytes; b"" when they are none."""
    try:
        return base64.b64decode(raw.decode("ascii").strip(), validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError):
        return b""


def _sign_file(manifest: Path, seed: bytes, public: bytes) -> Path:
    """Write manifest's .sig next to it; its path."""
    out = manifest.with_name(manifest.name + ".sig")
    signature = sign(manifest, seed)
    if not verify(manifest, signature, public):
        raise SignError(f"the signature over {manifest.name} does not verify against the pinned public key")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    if not verify(manifest, read_sig(tmp), public):
        tmp.unlink(missing_ok=True)
        raise SignError(f"{out.name} as written does not verify")
    tmp.replace(out)
    return out


def sign_site(site: Path, seed: bytes, public: bytes | None = None) -> Path:
    """Write site's timetables.json.sig, and its mav-index.json.sig when
    site has a mav-index.json, with seed, checked against public (default
    PUBLIC_KEY) first; timetables.json.sig's path. Raises SignError, with
    no .sig left in site."""
    public = PUBLIC_KEY if public is None else public
    for name in MANIFESTS:
        (site / (name + ".sig")).unlink(missing_ok=True)
    if not (site / MANIFEST_NAME).is_file():
        raise SignError(f"{site / MANIFEST_NAME} is not there")
    if public_key(seed) != public:
        raise SignError(f"{KEY_ENV} is not the key of the pinned public key")
    try:
        out = _sign_file(site / MANIFEST_NAME, seed, public)
        if (site / MAV_MANIFEST_NAME).is_file():
            _sign_file(site / MAV_MANIFEST_NAME, seed, public)
    except (OSError, SignError):
        for name in MANIFESTS:
            (site / (name + ".sig")).unlink(missing_ok=True)
        raise
    return out


def fetch(url: str, cap: int) -> tuple[bytes | None, str]:
    """The file at url (https only), at most cap bytes, or None and why not."""
    if not url.startswith("https://"):
        return None, "not an https URL"
    try:
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=LIVE_TIMEOUT) as res:  # noqa: S310 - https only
            body = res.read(cap + 1)
    except urllib.error.HTTPError as err:
        return None, f"HTTP {err.code}"
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        return None, f"{type(err).__name__}: {err}"
    if len(body) > cap:
        return None, f"larger than {cap} bytes"
    return body, "ok"


def _when(value: object) -> datetime | None:
    """An ISO time with an offset (a manifest's built_at), else None."""
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        return None
    return when if when is not None and when.tzinfo is not None else None


def built_at(raw: bytes) -> datetime | None:
    """A timetables.json's built_at (ISO, with an offset), else None."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return _when(data.get("built_at")) if isinstance(data, dict) else None


def _live_signed(base: str, name: str, get: Callable[[str, int], tuple[bytes | None, str]]) -> tuple[dict | None, str]:
    """The published manifest name (a JSON object), its .sig verifying
    against PUBLIC_KEY: (it, "ok"), else (None, why not)."""
    body, why = get(base + name, MAX_MANIFEST)
    if body is None:
        return None, f"the published {name} was not read ({why})"
    sig, why = get(base + name + ".sig", MAX_SIG)
    if sig is None or not verify_bytes(body, decode_sig(sig)):
        return None, f"the published {name} is not trusted ({why if sig is None else 'no good signature'})"
    try:
        data = json.loads(body)
    except ValueError:
        return None, f"the published {name} is not JSON"
    if not isinstance(data, dict):
        return None, f"the published {name} is not a JSON object"
    return data, "ok"


def _read_json(path: Path) -> dict:
    """A JSON object file of the site; {} for one that is not there or is
    no object."""
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return {}
    except ValueError:
        raise SignError(f"{path} is not JSON") from None
    return data if isinstance(data, dict) else {}


def _text(when: datetime | None) -> str:
    return when.isoformat() if when is not None else "at no valid time"


def live_newer(site: Path, live_url: str,
               get: Callable[[str, int], tuple[bytes | None, str]] = fetch) -> tuple[bool, str]:
    """(whether GitHub Pages at live_url serves files built later than
    site's, what was found): live-check. Each published manifest counts
    only when its .sig verifies; one that does not read or is not trusted
    holds nothing back. Times only in what was found: the published
    values are not echoed."""
    own_man = _read_json(site / MANIFEST_NAME)
    own = _when(own_man.get("built_at"))
    if own is None:
        raise SignError(f"{site / MANIFEST_NAME} names no built_at")
    base = live_url.rstrip("/") + "/"
    newer, found = False, []
    live, why = _live_signed(base, MANIFEST_NAME, get)
    if live is None:
        found.append(why)
    else:
        when = _when(live.get("built_at"))
        if when is None:
            found.append(f"the published {MANIFEST_NAME} names no built_at")
        else:
            newer |= when > own
            found.append(f"the published {MANIFEST_NAME} was built {when.isoformat()}, this site {own.isoformat()}")
        # A site job re-run of an older run writes a timetables.json of
        # its own time around that run's parts: each index by its own
        # built_at (a carried one has the published one's).
        own_indexes = own_man.get("indexes") if isinstance(own_man.get("indexes"), dict) else {}
        live_indexes = live.get("indexes") if isinstance(live.get("indexes"), dict) else {}
        for name in sorted(live_indexes):
            entry, mine = live_indexes[name], own_indexes.get(name)
            when = _when(entry.get("built_at")) if isinstance(entry, dict) else None
            if when is None or not isinstance(mine, dict):
                continue
            mine_at = _when(mine.get("built_at"))
            if mine_at is None or when > mine_at:
                newer = True
                found.append(f"its {name} index was built {when.isoformat()}, this site's {_text(mine_at)}")
    # The MÁV files: the same re-run takes its run's MÁV part as it is.
    live_mav, why = _live_signed(base, MAV_MANIFEST_NAME, get)
    own_mav = _read_json(site / MAV_MANIFEST_NAME)
    if live_mav is not None:
        when = _when(live_mav.get("built_at"))
        if when is not None:
            mine_at = _when(own_mav.get("built_at")) if own_mav else None
            if not own_mav or mine_at is None or when > mine_at:
                newer = True
            found.append(f"the published {MAV_MANIFEST_NAME} was built {when.isoformat()}, this site's "
                         + (_text(mine_at) if own_mav else "is not there"))
    elif own_mav:
        found.append(why)
    return newer, "; ".join(found)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sign", "verify", "live-check"):
        p = sub.add_parser(name)
        p.add_argument("--site", type=Path, required=True, help=f"the folder holding {MANIFEST_NAME}")
        if name == "live-check":
            p.add_argument("--live-url", required=True, help="the Pages site's https base URL")
    args = ap.parse_args(argv)
    if args.cmd == "live-check":
        try:
            newer, found = live_newer(args.site, args.live_url)
        except (OSError, SignError) as err:
            print(f"::error title={MANIFEST_NAME} not signed::{err}; nothing is deployed.")
            return 1
        if newer:
            print(f"::error title=An older site is not deployed::{found}: GitHub Pages already serves files of a"
                  " newer run (this is a re-run of an older run), so this site is not signed or deployed; nothing"
                  " to re-run.")
            return 1
        print(f"{found}; signing and deploying this site")
        return 0
    names = [n for n in MANIFESTS if n == MANIFEST_NAME or (args.site / n).is_file()]
    if args.cmd == "verify":
        bad = 0
        for name in names:
            ok = verify(args.site / name, read_sig(args.site / (name + ".sig")))
            print(f"{name}.sig: {'good signature' if ok else 'no good signature'} by the pinned key")
            bad += not ok
        return 0 if not bad else 1
    try:
        # A .sig of an earlier manifest must not go up next to this one.
        for name in MANIFESTS:
            (args.site / (name + ".sig")).unlink(missing_ok=True)
        sign_site(args.site, key_from_env())
    except (OSError, SignError) as err:
        print(f"::error title={MANIFEST_NAME} not signed::{err}; nothing is deployed, the site keeps its files.")
        return 1
    for name in names:
        print(f"{name}.sig: signed {name} ({(args.site / name).stat().st_size} bytes), the signature verified"
              " against the pinned public key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
