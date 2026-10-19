"""Cache-busting tag for the card URL, from what www/ actually holds."""
from __future__ import annotations

import hashlib
from pathlib import Path

# The files the card URL loads: the loader imports the bundle with the same ?v.
ASSET_FILES = ("hungarian-transport-loader.js", "hungarian-transport.js")
ASSET_TAG_LEN = 10


def asset_tag(www_path: Path) -> str:
    """Short sha256 of the entry files in www_path ("" when there are none).

    The version in manifest.json is stamped from the tag only in release zips;
    a www/ copied from a source build keeps the version of the last release,
    so ?v=<version> alone would never change and browsers would keep running
    the old bundle. The content hash changes with every new bundle."""
    digest = hashlib.sha256()
    found = False
    for name in ASSET_FILES:
        path = www_path / name
        if path.is_file():
            found = True
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()[:ASSET_TAG_LEN] if found else ""
