import argparse
import glob
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


CLIENT_ID = "rlViYQFTKkM"
API_URL = "https://shinjikai.app/rpc/LoadWordDetails"
IMAGE_BASE_URL = "https://shinjikai.app/static/word_pictures/"
DATA_DIR = Path("shinjikai_data")
IMAGE_DIR = Path("yomitan_images")
STATE_FILE = Path("scrape_state.json")
SUMMARY_FILE = Path("scrape_summary.json")
CHUNK_SIZE = 10_000


def env_int(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, default))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class ScrapeConfig:
    max_workers: int = env_int("SHINJIKAI_MAX_WORKERS", 6)
    batch_size: int = env_int("SHINJIKAI_BATCH_SIZE", 500)
    max_scan: int = env_int("SHINJIKAI_MAX_SCAN", 250_000)
    end_missing_threshold: int = env_int("SHINJIKAI_END_MISSING_THRESHOLD", 1_000)
    requests_per_second: int = env_int("SHINJIKAI_REQUESTS_PER_SECOND", 12)


class FetchStatus(str, Enum):
    FOUND = "found"
    MISSING = "missing"
    RATE_LIMITED = "rate_limited"
    BLOCKED = "blocked"
    TRANSIENT_ERROR = "transient_error"
    HTTP_ERROR = "http_error"


@dataclass(frozen=True)
class FetchResult:
    word_id: int
    status: FetchStatus
    data: Optional[dict] = None
    detail: str = ""


class ScrapeError(RuntimeError):
    pass


_thread_local = threading.local()
_rate_lock = threading.Lock()
_next_request_at = 0.0


def build_session() -> requests.Session:
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def get_session() -> requests.Session:
    if not hasattr(_thread_local, "session"):
        _thread_local.session = build_session()
    return _thread_local.session


def wait_for_request_slot(requests_per_second: int) -> None:
    global _next_request_at
    interval = 1.0 / requests_per_second
    with _rate_lock:
        now = time.monotonic()
        request_at = max(now, _next_request_at)
        _next_request_at = request_at + interval
    delay = request_at - now
    if delay > 0:
        time.sleep(delay)


def download_image(filename: str, config: ScrapeConfig) -> None:
    if not filename:
        return
    path = IMAGE_DIR / filename
    if path.exists():
        return
    try:
        wait_for_request_slot(config.requests_per_second)
        response = get_session().get(f"{IMAGE_BASE_URL}{filename}", timeout=20)
        if response.status_code == 200:
            path.write_bytes(response.content)
        else:
            print(f"Warning: image {filename} returned HTTP {response.status_code}")
    except requests.RequestException as exc:
        print(f"Warning: image {filename} failed: {exc}")


def fetch_word(word_id: int, config: ScrapeConfig) -> FetchResult:
    headers = {
        "Content-Type": "text/plain;charset=UTF-8",
        "X-Client-Id": CLIENT_ID,
    }
    try:
        wait_for_request_slot(config.requests_per_second)
        response = get_session().post(
            API_URL,
            json={"Id": word_id},
            headers=headers,
            timeout=20,
        )
    except requests.RequestException as exc:
        return FetchResult(word_id, FetchStatus.TRANSIENT_ERROR, detail=str(exc))

    if response.status_code == 429:
        return FetchResult(
            word_id,
            FetchStatus.RATE_LIMITED,
            detail=f"HTTP 429; Retry-After={response.headers.get('Retry-After', 'unknown')}",
        )
    if response.status_code in (401, 403):
        return FetchResult(word_id, FetchStatus.BLOCKED, detail=f"HTTP {response.status_code}")
    if response.status_code == 404 or (
        response.status_code == 400 and response.text.strip() == "WordNotFound"
    ):
        return FetchResult(word_id, FetchStatus.MISSING)
    if response.status_code >= 500:
        return FetchResult(word_id, FetchStatus.TRANSIENT_ERROR, detail=f"HTTP {response.status_code}")
    if response.status_code != 200:
        return FetchResult(word_id, FetchStatus.HTTP_ERROR, detail=f"HTTP {response.status_code}")

    try:
        data = response.json()
    except ValueError as exc:
        return FetchResult(word_id, FetchStatus.TRANSIENT_ERROR, detail=f"Invalid JSON: {exc}")

    if not data or "Word" not in data:
        return FetchResult(word_id, FetchStatus.MISSING)

    for meaning in data["Word"].get("Meanings", []):
        for picture in meaning.get("Pictures", []):
            filename = picture.get("Filename")
            if filename:
                download_image(filename, config)
    return FetchResult(word_id, FetchStatus.FOUND, data=data)


def read_finished_ids(data_dir: Path = DATA_DIR) -> set[int]:
    finished: set[int] = set()
    paths = [Path("raw_shinjikai_data.jsonl")]
    paths.extend(Path(path) for path in glob.glob(str(data_dir / "*.jsonl")))
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                try:
                    word_id = json.loads(line).get("Word", {}).get("Id")
                    if isinstance(word_id, int):
                        finished.add(word_id)
                except (json.JSONDecodeError, AttributeError):
                    print(f"Warning: ignoring malformed JSON at {path}:{line_number}")
    return finished


def load_state(path: Path = STATE_FILE) -> dict:
    if not path.exists():
        return {"version": 1, "known_missing_ids": [], "pending_retry_ids": []}
    with path.open("r", encoding="utf-8") as source:
        state = json.load(source)
    if state.get("version") != 1:
        raise ScrapeError(f"Unsupported scrape state version: {state.get('version')}")
    return state


def write_json_atomic(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def save_state(
    known_missing: set[int],
    pending_retry: set[int],
    high_water_mark: int,
    path: Path = STATE_FILE,
) -> None:
    write_json_atomic(
        path,
        {
            "version": 1,
            "high_water_mark": high_water_mark,
            "known_missing_ids": sorted(known_missing),
            "pending_retry_ids": sorted(pending_retry),
            "last_successful_check": datetime.now(timezone.utc).isoformat(),
        },
    )


def batched(values: Iterable[int], size: int) -> Iterable[list[int]]:
    batch: list[int] = []
    for value in values:
        batch.append(value)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def fetch_batch(
    word_ids: list[int],
    fetcher: Callable[[int], FetchResult],
    max_workers: int,
) -> list[FetchResult]:
    results: dict[int, FetchResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {executor.submit(fetcher, word_id): word_id for word_id in word_ids}
        for future in as_completed(future_to_id):
            word_id = future_to_id[future]
            try:
                results[word_id] = future.result()
            except Exception as exc:
                results[word_id] = FetchResult(
                    word_id,
                    FetchStatus.TRANSIENT_ERROR,
                    detail=f"Unhandled worker error: {exc}",
                )
    return [results[word_id] for word_id in word_ids]


def append_entries(results: Iterable[FetchResult], data_dir: Path = DATA_DIR) -> int:
    grouped: dict[Path, list[dict]] = {}
    for result in results:
        if result.status != FetchStatus.FOUND or result.data is None:
            continue
        path = data_dir / f"data_{result.word_id // CHUNK_SIZE}.jsonl"
        grouped.setdefault(path, []).append(result.data)

    written = 0
    for path, entries in grouped.items():
        with path.open("a", encoding="utf-8", newline="\n") as output:
            for entry in entries:
                output.write(json.dumps(entry, ensure_ascii=False) + "\n")
                written += 1
    return written


def describe_failures(results: Iterable[FetchResult]) -> str:
    failures = [result for result in results if result.status not in (FetchStatus.FOUND, FetchStatus.MISSING)]
    return "; ".join(
        f"ID {result.word_id}: {result.status.value} ({result.detail or 'no detail'})"
        for result in failures[:10]
    )


def run_scrape(
    config: ScrapeConfig,
    fetcher: Optional[Callable[[int], FetchResult]] = None,
    data_dir: Path = DATA_DIR,
    state_path: Path = STATE_FILE,
) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    actual_fetcher = fetcher or (lambda word_id: fetch_word(word_id, config))

    finished = read_finished_ids(data_dir)
    state = load_state(state_path)
    known_missing = set(state.get("known_missing_ids", []))
    pending_retry = set(state.get("pending_retry_ids", []))
    high_water_mark = max(finished, default=0)
    found_count = 0
    scanned_count = 0

    print(f"Database currently holds {len(finished)} finished entries.", flush=True)
    print(f"Highest stored ID is {high_water_mark}.", flush=True)

    historical_gaps = set(range(1, high_water_mark + 1)) - finished - known_missing
    repair_ids = sorted((historical_gaps | pending_retry) - finished)
    if repair_ids:
        print(f"Repairing {len(repair_ids)} historical or transient gaps...", flush=True)
    for repair_batch in batched(repair_ids, config.batch_size):
        results = fetch_batch(repair_batch, actual_fetcher, config.max_workers)
        scanned_count += len(results)
        found_count += append_entries(results, data_dir)
        for result in results:
            if result.status == FetchStatus.FOUND:
                finished.add(result.word_id)
                known_missing.discard(result.word_id)
                pending_retry.discard(result.word_id)
            elif result.status == FetchStatus.MISSING:
                known_missing.add(result.word_id)
                pending_retry.discard(result.word_id)
            else:
                pending_retry.add(result.word_id)
        failure_text = describe_failures(results)
        if failure_text:
            save_state(known_missing, pending_retry, max(finished, default=0), state_path)
            raise ScrapeError(f"Gap repair stopped safely: {failure_text}")

    frontier_start = max(finished, default=0) + 1
    print(f"Scanning forward from ID {frontier_start} in bounded batches...", flush=True)
    consecutive_missing = 0
    reached_end = False
    last_progress_at = time.monotonic()
    frontier = range(frontier_start, frontier_start + config.max_scan)

    for frontier_batch in batched(frontier, config.batch_size):
        results = fetch_batch(frontier_batch, actual_fetcher, config.max_workers)
        scanned_count += len(results)
        found_count += append_entries(results, data_dir)

        for result in results:
            if result.status == FetchStatus.FOUND:
                finished.add(result.word_id)
                pending_retry.discard(result.word_id)
                consecutive_missing = 0
            elif result.status == FetchStatus.MISSING:
                consecutive_missing += 1
            else:
                pending_retry.add(result.word_id)

        failure_text = describe_failures(results)
        if failure_text:
            save_state(known_missing, pending_retry, max(finished, default=0), state_path)
            raise ScrapeError(f"Forward scan stopped safely: {failure_text}")

        now = time.monotonic()
        if now - last_progress_at >= 10:
            print(
                f"Scanned {scanned_count} IDs; found {found_count}; "
                f"ordered missing streak {consecutive_missing}/{config.end_missing_threshold}",
                flush=True,
            )
            last_progress_at = now

        if consecutive_missing >= config.end_missing_threshold:
            health_id = max(finished, default=0)
            if health_id == 0:
                raise ScrapeError("Cannot verify service health because no known word exists")
            health = actual_fetcher(health_id)
            if health.status != FetchStatus.FOUND:
                pending_retry.add(health_id)
                save_state(known_missing, pending_retry, health_id, state_path)
                raise ScrapeError(
                    "The apparent database end could not be verified: "
                    f"known ID {health_id} returned {health.status.value} ({health.detail})"
                )
            reached_end = True
            print(
                f"Reached {consecutive_missing} consecutive missing IDs in numeric order; "
                f"service health verified with ID {health_id}.",
                flush=True,
            )
            break

    save_state(known_missing, pending_retry, max(finished, default=0), state_path)
    summary = {
        "status": "success",
        "entries_found": found_count,
        "ids_scanned": scanned_count,
        "high_water_mark": max(finished, default=0),
        "reached_verified_end": reached_end,
    }
    print(json.dumps(summary, sort_keys=True), flush=True)
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Incrementally update the Shinjikai source database")
    parser.add_argument("--summary-file", type=Path, default=SUMMARY_FILE)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        summary = run_scrape(ScrapeConfig())
        write_json_atomic(args.summary_file, summary)
        return 0
    except Exception as exc:
        summary = {"status": "error", "error": str(exc)}
        write_json_atomic(args.summary_file, summary)
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
