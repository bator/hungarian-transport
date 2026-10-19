#!/usr/bin/env python3
"""The card URL's cache-busting tag (O-11). No Home Assistant needed."""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "ht_assets", Path(__file__).resolve().parents[1] / "custom_components" / "hungarian_transport" / "assets.py"
)
assets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(assets)

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(("ok" if ok else "FAIL") + f"  {name}" + (f" {detail}" if detail and not ok else ""))
    failed += not ok


with tempfile.TemporaryDirectory() as tmp:
    www = Path(tmp)
    check("no entry files, no tag", assets.asset_tag(www) == "")
    (www / "hungarian-transport.js").write_text("// card 1", encoding="utf-8")
    only_bundle = assets.asset_tag(www)
    check("a tag of ASSET_TAG_LEN hex digits", len(only_bundle) == assets.ASSET_TAG_LEN
          and all(c in "0123456789abcdef" for c in only_bundle), repr(only_bundle))
    check("the same content, the same tag", assets.asset_tag(www) == only_bundle)
    (www / "hungarian-transport-loader.js").write_text("// loader", encoding="utf-8")
    with_loader = assets.asset_tag(www)
    check("the loader counts too", with_loader != only_bundle)
    (www / "hungarian-transport.js").write_text("// card 2", encoding="utf-8")
    check("a rebuilt bundle (same manifest version) is a new tag", assets.asset_tag(www) != with_loader)
    before = assets.asset_tag(www)
    (www / "volan-index.json.gz").write_bytes(b"index")
    check("other files in www/ do not change it", assets.asset_tag(www) == before)

if failed:
    sys.exit(1)
print("asset tag tests passed")
