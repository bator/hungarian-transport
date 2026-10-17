#!/usr/bin/env python3
"""Restoring the durable indexes after a HACS update. No network."""
from __future__ import annotations

import gzip
import json
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from update_volan_index import index_schema, persist_index, restore_hacs_indexes  # noqa: E402

failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    print(f"{'ok' if ok else 'FAIL'}  {name}{'' if ok else ' ' + detail}")
    if not ok:
        failed += 1


def write_index(path: Path, schema: int, feed: str, pad: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Incompressible padding, so sizes differ after gzip as well.
    rnd = random.Random(pad)
    body = {"v": schema, "feed": feed, "pad": "".join(rnd.choice("0123456789abcdef") for _ in range(pad))}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0))


def feed_of(path: Path) -> str:
    return json.loads(gzip.decompress(path.read_bytes()))["feed"]


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "durable"
    www = Path(tmp) / "www"
    write_index(base / "volan-index.json.gz", 27, "local")
    check("schema read from the index head", index_schema(base / "volan-index.json.gz") == 27)

    restored = restore_hacs_indexes(base, www)
    check("missing index is restored", restored == ["volan-index.json.gz"]
          and feed_of(www / "volan-index.json.gz") == "local", repr(restored))

    check("identical index is left alone", restore_hacs_indexes(base, www) == [])

    write_index(www / "volan-index.json.gz", 27, "release", pad=50_000)
    restored = restore_hacs_indexes(base, www)
    check("larger shipped index with the same schema is replaced by the durable one",
          restored == ["volan-index.json.gz"] and feed_of(www / "volan-index.json.gz") == "local",
          repr(restored))

    write_index(www / "volan-index.json.gz", 28, "release-new-schema")
    restored = restore_hacs_indexes(base, www)
    check("shipped index with a newer schema is kept",
          restored == [] and feed_of(www / "volan-index.json.gz") == "release-new-schema",
          repr(restored))

    write_index(base / "mav-index.json.gz", 1, "pfaedle", pad=50_000)
    write_index(www / "mav-index.json.gz", 1, "official-stub")
    restored = restore_hacs_indexes(base, www)
    check("larger local mav index wins", "mav-index.json.gz" in restored
          and feed_of(www / "mav-index.json.gz") == "pfaedle", repr(restored))

    write_index(www / "mav-index.json.gz", 1, "release-pfaedle", pad=200_000)
    restored = restore_hacs_indexes(base, www)
    check("larger shipped mav index is kept", "mav-index.json.gz" not in restored
          and feed_of(www / "mav-index.json.gz") == "release-pfaedle", repr(restored))


def write_with_shapes(path: Path, schema: int, feed: str, folder: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"v": schema, "sh": {"dir": folder, "n": 64}, "feed": feed}
    path.write_bytes(gzip.compress(json.dumps(body, separators=(",", ":")).encode(), mtime=0))
    (path.parent / folder).mkdir(exist_ok=True)
    (path.parent / folder / "00.json.gz").write_bytes(gzip.compress(b'{"v":1,"p":[]}'))


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp) / "durable"
    www = Path(tmp) / "www"
    base.mkdir()
    write_with_shapes(www / "volan-index.json.gz", 29, "built", "volan-shapes-new")
    (base / "volan-shapes-old").mkdir()
    persist_index(www / "volan-index.json.gz", base)
    check("persist copies the shapes folder and drops the old one",
          (base / "volan-shapes-new" / "00.json.gz").is_file() and not (base / "volan-shapes-old").exists())

    # HACS update: the served folder now holds the release index and shapes.
    for entry in list(www.iterdir()):
        if entry.is_dir():
            import shutil
            shutil.rmtree(entry)
        else:
            entry.unlink()
    write_with_shapes(www / "volan-index.json.gz", 29, "release", "volan-shapes-release")
    restored = restore_hacs_indexes(base, www)
    check("restore brings the durable index together with its shapes folder",
          restored == ["volan-index.json.gz"] and (www / "volan-shapes-new" / "00.json.gz").is_file(),
          repr(restored))
    check("the release shapes folder nobody points at is removed",
          not (www / "volan-shapes-release").exists(), repr(sorted(p.name for p in www.iterdir())))

if failed:
    sys.exit(1)
print("update_volan_index restore tests passed")
