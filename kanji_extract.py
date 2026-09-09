"""Resumable, lossless public LoadKanji census. See KANJI.md for coverage limits."""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

BASE = "https://shinjikai.app"
# Order affects discovery speed only: every non-surrogate code point is visited.
PRIORITY = [(0x4E00, 0xA000), (0x3400, 0x4DC0), (0xF900, 0xFB00),
            (0x20000, 0x32400), (0x2E80, 0x3400)]
SEEDS = [ord(c) for c in "楽日月水火木金土人山川愛龍𠮷"]
SCHEMA = 1


def candidates():
    seen = set()
    for cp in SEEDS:
        seen.add(cp)
        yield cp
    for start, end in PRIORITY + [(0, 0x110000)]:
        for cp in range(start, end):
            if 0xD800 <= cp < 0xE000 or cp in seen:
                continue
            seen.add(cp)
            yield cp


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def picture_names(value):
    """Walk all fields, including future/hidden meaning and note containers."""
    if isinstance(value, dict):
        if "Filename" in value:
            name = value["Filename"]
            if not isinstance(name, str) or not name or Path(name).name != name or "\\" in name:
                raise ValueError(f"Unsafe or invalid asset filename: {name!r}")
            yield name
        for child in value.values():
            yield from picture_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from picture_names(child)


def validate_response(cp, response):
    if response.status_code == 400 and response.text.strip() == "KanjiNotFound":
        return None
    if response.status_code != 200:
        raise RuntimeError(f"U+{cp:04X}: HTTP {response.status_code}: {response.text[:160]}")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("Kanji"), dict):
        raise ValueError(f"U+{cp:04X}: invalid response shape")
    k = data["Kanji"]
    if k.get("Character") != cp:
        raise ValueError(f"U+{cp:04X}: returned different character {k.get('Character')}")
    for field in ("Readings", "Meanings", "Notes"):
        if not isinstance(k.get(field), list):
            raise ValueError(f"U+{cp:04X}: missing or invalid {field}")
    return data


class Client:
    def __init__(self, rate=20):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.interval = 1 / rate
        self.next_at = 0
        self.lock = threading.Lock()
        self.local = threading.local()
        self.stopped = threading.Event()
        self.asset_locks = [threading.Lock() for _ in range(64)]

    @property
    def session(self):
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
            self.local.session.headers['X-Client-Id'] = 'rlViYQFTKkM'
        return self.local.session

    def request(self, method, path, **kwargs):
        for attempt in range(5):
            with self.lock:
                if self.stopped.is_set():
                    raise RuntimeError('Requests stopped after access denial')
                time.sleep(max(0, self.next_at - time.monotonic()))
                self.next_at = time.monotonic() + self.interval
            try:
                r = self.session.request(method, BASE + path, timeout=(15, 45), **kwargs)
            except requests.RequestException:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt + random.random())
                continue
            if r.status_code in (401, 403):
                self.stopped.set()
                raise RuntimeError(f"Access denied at {path}; stopping without bypassing restrictions")
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == 4:
                    raise RuntimeError(f"Persistent HTTP {r.status_code} at {path}")
                retry = requests.adapters.Retry().get_retry_after(r)
                with self.lock:
                    self.interval = min(4, self.interval * 2)
                    self.next_at = max(self.next_at, time.monotonic() + max(retry or 0, 2 ** attempt + random.random()))
                continue
            return r
        raise RuntimeError("Retries exhausted")

    def kanji(self, cp):
        r = self.request("POST", "/rpc/LoadKanji", json={"Kanji": chr(cp)},
                         headers={"Content-Type": "text/plain;charset=UTF-8"})
        return validate_response(cp, r)


def open_db(root):
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "checkpoint.sqlite")
    db.execute("CREATE TABLE IF NOT EXISTS entries (cp INTEGER PRIMARY KEY, payload TEXT, assets TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
    version = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()
    if version and int(version[0]) != SCHEMA:
        raise ValueError("Unsupported checkpoint schema")
    db.execute("INSERT OR IGNORE INTO metadata VALUES ('schema', ?)", (str(SCHEMA),))
    db.commit()
    return db


def asset(client, root, path, optional=False):
    # Different entries can reference the same image. Protect its atomic write.
    with client.asset_locks[int(sha(path.encode())[:8], 16) % 64]:
        return download_asset(client, root, path, optional)


def download_asset(client, root, path, optional=False):
    local = "assets/" + sha(path.encode()) + Path(path).suffix
    target = root / local
    if target.exists():
        body = target.read_bytes()
    else:
        r = client.request("GET", path)
        if optional and r.status_code == 404:
            return {"url": BASE + path, "status": "absent", "http_status": 404}
        if r.status_code != 200 or not r.headers.get("Content-Type", "").lower().startswith("image/"):
            raise RuntimeError(f"Required image failed: {path} HTTP {r.status_code}")
        body = r.content
        if not body:
            raise ValueError(f"Empty image at {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + ".tmp")
        temp.write_bytes(body)
        temp.replace(target)
    return {"url": BASE + path, "status": "downloaded", "path": local,
            "sha256": sha(body), "bytes": len(body)}


def collect(client, root, cp, data):
    assets = []
    for name in sorted(set(picture_names(data))):
        assets.append(asset(client, root, "/static/word_pictures/" + quote(name, safe="")))
    for extension in ("gif", "png"):
        assets.append(asset(client, root, "/static/kanji_animations/" + quote(chr(cp), safe="") + "." + extension, True))
    return assets


def export(db, root, output, error=None):
    total = 0x110000 - 0x800
    checked = db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    found = db.execute("SELECT COUNT(*) FROM entries WHERE payload IS NOT NULL").fetchone()[0]
    manifest = {"schema_version": SCHEMA, "source": BASE,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "target": "all Unicode scalar values U+0000..U+10FFFF, excluding surrogates",
                "total_candidates": total, "checked": checked, "entries": found,
                "confirmed_missing": checked - found, "remaining": total - checked,
                "complete": checked == total and error is None, "error": error,
                "consistency": "Sequential observations, not an atomic server snapshot"}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("manifest.json", encoded(manifest))
        with archive.open("kanji.jsonl", "w") as out:
            paths = set()
            for cp, payload, media in db.execute("SELECT cp,payload,assets FROM entries WHERE payload IS NOT NULL ORDER BY cp"):
                assets = json.loads(media)
                for item in assets:
                    if item["status"] != "downloaded":
                        continue
                    path = item["path"]
                    body = (root / path).read_bytes()
                    if sha(body) != item["sha256"]:
                        raise ValueError(f"Asset checksum mismatch: {path}")
                    paths.add(path)
                record = {"character": chr(cp), "codepoint": cp,
                          "source_url": BASE + "/kanji/" + quote(chr(cp), safe=""),
                          "response": json.loads(payload), "assets": assets}
                out.write((encoded(record) + "\n").encode())
        for path in sorted(paths):
            archive.write(root / path, path, compress_type=zipfile.ZIP_STORED)
        archive.write(root / "checkpoint.sqlite", "checkpoint.sqlite")
    Path("kanji_manifest.json").write_text(encoded(manifest) + "\n", encoding="utf-8")
    print(encoded(manifest), flush=True)
    return manifest


def restore(archive_path, root):
    if not archive_path.exists():
        return
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name != "checkpoint.sqlite" and not name.startswith("assets/"):
                continue
            target = (root / name).resolve()
            if not target.is_relative_to(root.resolve()) or "\\" in name:
                raise ValueError("Unsafe checkpoint archive path")
            archive.extract(info, root)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--minutes", type=float, default=220)
    parser.add_argument("--rate", type=float, default=20)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--root", type=Path, default=Path("kanji_work"))
    parser.add_argument("--resume", type=Path, default=Path("previous.zip"))
    args = parser.parse_args()
    if args.limit <= 0 or args.minutes <= 0 or not 1 <= args.workers <= 16:
        parser.error("limit/minutes must be positive; workers must be 1..16")
    restore(args.resume, args.root)
    db = open_db(args.root)
    client = Client(args.rate)
    started = time.monotonic()
    error = None
    try:
        # Check a known entry before accepting any negative census results.
        if client.kanji(ord("楽")) is None:
            raise RuntimeError("Known-entry health check failed")
        processed = 0
        visited = {row[0] for row in db.execute('SELECT cp FROM entries')}
        remaining = (cp for cp in candidates() if cp not in visited)

        def fetch(cp):
            data = client.kanji(cp)
            media = collect(client, args.root, cp, data) if data else []
            return cp, encoded(data) if data else None, encoded(media)

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while processed < args.limit and time.monotonic() - started < args.minutes * 60:
                batch = list(islice(remaining, min(128, args.limit - processed)))
                if not batch:
                    break
                # Bounded futures; only the main thread writes SQLite. A batch
                # fails atomically if any record or required image is incomplete.
                results = list(pool.map(fetch, batch))
                db.executemany('INSERT INTO entries VALUES (?,?,?)', results)
                db.commit()
                processed += len(results)
                if client.kanji(ord("楽")) is None:
                    raise RuntimeError("Known-entry health check failed during census")
                elapsed = time.monotonic() - started
                print(f"Checked {processed} new characters; {processed / elapsed:.1f} chars/s; "
                      f"latest U+{batch[-1]:04X}; request cap {1 / client.interval:.1f}/s", flush=True)
        if client.kanji(ord("楽")) is None:
            raise RuntimeError("Final known-entry health check failed")
    except Exception as exc:
        error = str(exc)
    db.commit()
    export(db, args.root, "Shinjikai_Kanji.zip", error)
    db.close()
    if error:
        raise SystemExit(error)


if __name__ == "__main__":
    main()
