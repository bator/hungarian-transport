#!/usr/bin/env python3
"""Sign timetables.json on GitHub Pages (mav-index-daily.yml's site job).

The integration installs the Volán and city indexes the GitHub Pages
manifest timetables.json names, each file checked against the manifest's
sha256 (custom_components/hungarian_transport/weekly_timetables.py). The
manifest itself is signed: timetables.json.sig next to it is one line, the
base64 of the 64-byte ed25519 signature over timetables.json's exact bytes
as deployed, and the integration checks it against the public key it pins
(PUBLIC_KEY here) before it reads the manifest. Whoever could put files on
the Pages site, or answer for it, cannot make installs take an index
without the private key, which only the site job's signing step sees (the
repository secret PAGES_SIGNING_KEY: base64 of the raw 32-byte ed25519
private key, its seed).

  sign   --site DIR
         Writes DIR/timetables.json.sig. The key comes from the environment
         (PAGES_SIGNING_KEY), never the command line, and is never printed.
         Before it writes, the key's public half must be PUBLIC_KEY and the
         signature must verify against PUBLIC_KEY. Exit 1, with no .sig in
         DIR, for no key, a key that does not decode to 32 bytes, another
         key than the pinned one's, a signature that does not verify, no
         timetables.json, or no usable openssl: no unsigned manifest is
         ever deployed (the workflow uploads the site only after this).
  verify --site DIR
         Exit 0 when DIR/timetables.json.sig is a signature over
         DIR/timetables.json by PUBLIC_KEY's key, 1 otherwise.

The ed25519 work is openssl's (3.0 or later, for pkeyutl -rawin; GitHub's
Ubuntu runners have it): nothing is installed into the job that holds the
key. The private key reaches openssl on its stdin (PKCS#8 DER), never a
file or an argument.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

MANIFEST_NAME = "timetables.json"
SIG_NAME = MANIFEST_NAME + ".sig"
KEY_ENV = "PAGES_SIGNING_KEY"
# The public key of PAGES_SIGNING_KEY: base64 of the raw 32 bytes. The
# integration pins the same key; a new key pair changes both.
PUBLIC_KEY = base64.b64decode("QCs8j2ex1KxTRA7pHfsZ6f2SMu5jidCwy1LyvIVOd+M=")
# The DER around a raw ed25519 key (RFC 8410): a PKCS#8 private key of a
# 32-byte seed, a SubjectPublicKeyInfo of a 32-byte public key.
_PKCS8_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")
SIG_BYTES = 64
OPENSSL_TIMEOUT = 60


class SignError(Exception):
    """The manifest cannot be signed. The text never holds the key."""


def _openssl(args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["openssl", *args], input=stdin, capture_output=True, timeout=OPENSSL_TIMEOUT,
                              check=False)
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


def verify(data: Path, signature: bytes, public: bytes = PUBLIC_KEY) -> bool:
    """Whether signature is public's key's ed25519 signature over data."""
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
        return base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (OSError, UnicodeDecodeError, binascii.Error, ValueError):
        return b""


def sign_site(site: Path, seed: bytes, public: bytes = PUBLIC_KEY) -> Path:
    """Write site's timetables.json.sig with seed, checked against public
    first; its path. Raises SignError, with no .sig left in site."""
    manifest, out = site / MANIFEST_NAME, site / SIG_NAME
    out.unlink(missing_ok=True)
    if not manifest.is_file():
        raise SignError(f"{manifest} is not there")
    if public_key(seed) != public:
        raise SignError(f"{KEY_ENV} is not the key of the pinned public key")
    signature = sign(manifest, seed)
    if not verify(manifest, signature, public):
        raise SignError("the signature does not verify against the pinned public key")
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(base64.b64encode(signature).decode("ascii") + "\n", encoding="ascii")
    if not verify(manifest, read_sig(tmp), public):
        tmp.unlink(missing_ok=True)
        raise SignError(f"{SIG_NAME} as written does not verify")
    tmp.replace(out)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("sign", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--site", type=Path, required=True, help=f"the folder holding {MANIFEST_NAME}")
    args = ap.parse_args(argv)
    if args.cmd == "verify":
        ok = verify(args.site / MANIFEST_NAME, read_sig(args.site / SIG_NAME))
        print(f"{SIG_NAME}: {'good signature' if ok else 'no good signature'} by the pinned key")
        return 0 if ok else 1
    try:
        # A .sig of an earlier manifest must not go up next to this one.
        (args.site / SIG_NAME).unlink(missing_ok=True)
        out = sign_site(args.site, key_from_env())
    except (OSError, SignError) as err:
        print(f"::error title={MANIFEST_NAME} not signed::{err}; nothing is deployed, the site keeps its files.")
        return 1
    print(f"{out.name}: signed {MANIFEST_NAME} ({(args.site / MANIFEST_NAME).stat().st_size} bytes), the signature"
          " verified against the pinned public key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
