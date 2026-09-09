# Kanji offline backup

Run **Shinjikai Kanji Offline Backup** in Actions. Scheduled runs every six
hours resume the last successful `kanji-` release. The `limit` input bounds
new characters per run; the time budget also checkpoints progress.

The fast configuration uses eight worker threads, a shared 20 requests/second
ceiling (including images and retries), batches of 128 database writes, and
100,000 new characters per run. Each worker has its own connection session.
Rate limiting slows all workers together; access denial stops requests.
ZIP packaging stores already-compressed images directly and uses fast
compression for JSON and checkpoint data. Existing checkpoints remain compatible.

Download `Shinjikai_Kanji.zip` and extract it. It contains:

- `kanji.jsonl`: one UTF-8 JSON record per entry, preserving the entire public
  `LoadKanji` response without dropping unknown fields, notes, captions,
  reading variants, examples, source attribution, or relationships.
- `assets/`: actual downloaded illustration bytes, stroke-order GIFs and
  preview PNGs. Each record maps source URLs to relative offline asset paths.
- `manifest.json`: exact coverage, errors, and completeness status.
- `checkpoint.sqlite`: all visited characters, including confirmed negatives.

This is an offline data archive, not a cloned website or a Yomitan import.
Linked word IDs and source URLs are preserved as references; full linked word
articles belong to the separate word dictionary backup. No network access is
needed to read the archived JSON or display the downloaded images.

## Public-site investigation (2026-09-09)

The `/kanji/楽` HTML is an empty JavaScript shell. Its entry bundle imports
`kanji-YWAYVOOH.js`, then `chunk-OIFTPQER.js`. That renderer requests
`POST /rpc/LoadKanji` with `{"Kanji":"楽"}`. We compared the rendered entry
with the response: meanings, etymology, Japanese sources, readings and word
examples, composition, variants, confusion, radical and level data are present.
The raw response even includes reading variants not displayed by the renderer.

Illustrations use `/static/word_pictures/{Filename}` (including captions in
the response). Stroke GIFs and card PNGs are constructed separately as
`/static/kanji_animations/{character}.{gif,png}`. These are explicitly fetched.
For 楽, the etymology JPG and GIF both returned actual images; 日 also has a
captioned etymology JPG. Supplementary-plane 𠮷 has a valid API entry.

## Completeness and failure handling

The census visits every Unicode scalar value, prioritizing common CJK,
extensions and compatibility characters. It never stops at a missing streak.
Only HTTP 400 with exact `KanjiNotFound` is a missing entry. Unexpected HTTP,
JSON shape, or mismatched code points fail the run. Required illustrations
must download successfully before the entry is committed. Optional constructed
GIF/PNG paths may be absent only on HTTP 404. ZIP export checks asset hashes.

Intermediate releases are prereleases and explicitly say `complete: false`.
Full coverage is not promised before all 1,112,064 scalar values are checked.
This takes many scheduled runs at the conservative request rate. The source
can change during a census; even a completed census is not an atomic snapshot.
Already visited entries are reused during this census. To start a fresh census,
run locally without `--resume` or use a fresh checkpoint; the current action
continues its existing census. Do not interpret a successful checkpoint as
proof that the entire live website has been captured.

Access-denied responses stop the run. Requests have bounded retries, jitter,
Retry-After handling and shared rate reduction. Failed runs preserve diagnostic
artifacts and do not publish a successful release or replace a good checkpoint.
Kanji releases use `--latest=false` so word dictionary update URLs remain intact.
