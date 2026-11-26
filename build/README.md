# build

The write path. These are plain scripts, not a package (a package named `build` would shadow
PyPA's `build`). They import `chapterverse`, so run them with `uv run` from the repository root.

| Script | What it does |
|---|---|
| `textstore.py build` | Builds the text store from `$LEGAL_DATA_ROOT/uk` into `artefacts/store/` |
| `textstore.py bench` | Measures load time, RSS and `get()` latency of a built store |
| `manifest.py show`, `manifest.py verify` | Prints a store's manifest and `corpus_id`; re-hashes every file |
| `publish_dataset.py` | Uploads the store, router index, concepts, SymSpell dictionary, dense index, query model and clarify prior to a Hugging Face Dataset (dry run by default) |

## Text store

The store holds the router's 134,219 JSONL record files (3.84 GB) for exact lookup by
coordinate. Rows are stored byte for byte, so `checksum_sha256` still verifies, and
`chapterverse.store.open_store(path).get(c)` returns exactly what
`legal_rag_verifier.corpus.load_provision(LEGAL_DATA_ROOT, c)` returns. The source data is read
only and never copied.

| File | Bytes | Contents |
|---|---|---|
| `text.zst` | 569,117,151 | 430,222 independent zstd frames; decompressed and joined they are the source files, in coordinate order |
| `frames.u64` | 6,883,568 | Start of each frame in the decompressed stream and in `text.zst` (uint64) |
| `coordinates.tbl`, `coordinates.off` | 217,748,022 and 22,793,472 | The router's `SortedTable` format: 5,698,368 coordinates to byte runs |
| `dictionary.zdict` | 112,640 | The zstd dictionary all frames use |
| `corpus-manifest.json` | 1,742 | Format version, snapshot, source and OGL v3.0 attribution, counts, size and SHA-256 of every file |

The format is specified in `engine/chapterverse/store/format.py`. An instrument entry gives the
file's place in the stream and the length of its instrument record line. A provision entry
gives the byte runs of the provision and all its descendants within that file. Most need one
run. 8,002 coordinates in 2,162 files (old `nisi` and SIs whose Parts and chapters interleave
with other rows) need several, so a run list is stored rather than one span.

`corpus_id` is the SHA-256 of the manifest's canonical JSON, which are the file's exact bytes.
Opening a store checks the size and SHA-256 of every file against the manifest, so a damaged
store fails at load, never at query time.

The build refuses data it could not return exactly as `load_provision` reads it: a file without
a final newline, a carriage return, a first line that is not the instrument record, a row outside
the instrument, a duplicate coordinate, a file `load_provision` would not resolve to, or two
files for one instrument. The real corpus has none of these.

## Build

```
uv run python build/textstore.py build --data "$LEGAL_DATA_ROOT" --out artefacts/store
```

Defaults: `--blocks packed:16384 --dict-size 112640 --level 19`. Measured on an Apple M4,
10 cores, 16 GB, macOS 26.6, Python 3.12.11, zstd 1.5.7, on AC power:

| Measure | Value |
|---|---|
| Wall time | 122 s (1,058 s CPU, 10 worker processes) |
| Peak RSS | 486 MiB parent, 186 MiB largest worker |
| Store size | 816,654,853 bytes in 5 files plus the manifest |
| Determinism | A second build with 7 workers gave byte-identical files |

The dictionary sample is chosen by the SHA-256 of each instrument coordinate, and training uses
fixed cover parameters (k 1024, d 8, one thread), so it is deterministic.

## Block granularity: measurements

Each row is a full build over the real corpus followed by `textstore.py bench`. The machine was on
battery for most of these runs. The chosen configuration was rebuilt and re-measured on AC power
(next section): the same `text.zst` size, and p50 within 3 us. "Sample" is 2,000 coordinates drawn uniformly from the
table with a fixed seed.

| Frames | Dictionary | Level | text.zst MB | Build s | `get(s124)` p50 / p99 us | Sample p50 / p99 us |
|---|---|---|---|---|---|---|
| One per file | yes | 19 | 499.4 | 122 | 946 / 1,046 | 166 / 2,916 |
| One per top-level provision | yes | 19 | 591.1 | 126 | 93 / 124 | 41 / 354 |
| Fixed 64 KiB | yes | 19 | 526.8 | 130 | 92 / 97 | 56 / 204 |
| Packed 128 KiB | yes | 19 | 516.0 | 136 | 106 / 111 | 73 / 222 |
| Packed 64 KiB | yes | 19 | 526.5 | 124 | 92 / 114 | 55 / 208 |
| Packed 64 KiB | no | 19 | 687.3 | 160 | 93 / 98 | 54 / 202 |
| Packed 64 KiB | yes | 9 | 582.6 | 24 | 94 / 97 | 56 / 202 |
| Packed 32 KiB | yes | 19 | 543.9 | 132 | 86 / 91 | 44 / 197 |
| Packed 16 KiB | yes | 19 | 569.1 | 128 | 80 / 93 | 38 / 187 |

"Packed N" puts whole top-level provisions (a section and its descendants) in a frame up to N
bytes and splits a larger one at line ends; the instrument record line has its own frame.

The choice is packed 16 KiB with a dictionary at level 19. One frame per file is the smallest
but makes a section lookup decompress the whole Act (p99 2.9 ms over the sample). One frame per
top-level provision gives 894,107 small frames and a worse p99. The dictionary cuts `text.zst`
by 23% at no cost in latency. Among packed sizes, 16 KiB is the fastest at p50 and p99 for 43 MB
(8%) more than 64 KiB; the order is stability, accuracy, speed, then cost. Level 19 against level
9 costs about 100 s once per build and saves 56 MB.

## Read performance

`textstore.py bench --store artefacts/store`, same machine, AC power, one thread:

| Measure | Value |
|---|---|
| Load (hash every file, map) | 0.21 s |
| RSS before load, after load | 36 MiB, 67 MiB |
| `get("uk/ukpga/1996/18/s124")`, 2,000 calls | p50 77 us, p99 81 us |
| `get` over the sample, first and second pass | p50 38 and 33 us, p99 185 and 181 us |
| `get` of a whole instrument, ERA 1996 (5,833 rows) | p50 15.7 ms, p99 18.9 ms |
| `get` of a whole instrument, Corporation Tax Act 2010 (20,828 rows, largest file) | p50 59 ms, p99 65 ms |
| `instrument()` over the sample | p50 15 us, p99 151 us |
| `load_provision` for s124, for comparison | p50 13.4 ms |

Where `get(s124)` goes (17 rows, 2 frames, 15,856 bytes): `json.loads` 47 us, zstd 15 us, the
two table lookups 6 us. The p99 over the sample comes mostly from parsing large instrument
records (up to 46 KB; `instrument()` alone has p99 151 us). Against the router's 2 ms path, a section lookup adds 4% at p50 and 9% at p99 of
the sample. A whole large instrument is dominated by parsing its rows and should not be read on
the request path.

RSS grows as mapped pages are touched: 60 MiB after load, 61 MiB after 2,000 `s124` calls,
108 MiB after the whole-instrument reads, 329 MiB after 2,000 random lookups. The pages are
clean, file-backed and reclaimable, and are bounded by the store size. Most come from the
binary search over the 218 MB table; this Mac has 16 KiB pages.

Tried and not adopted:

| Change | Result |
|---|---|
| LRU of 1,024 decompressed frames | `s124` repeated: 82 to 67 us. Random sample: no gain (34.5 against 33.6 us warm). Not worth a lock and 16 MiB |
| `madvise(MADV_RANDOM)` on the maps | Same RSS and latency |
| Decode the whole read to one str, then split | Same speed, but RSS reached 479 MiB against 90 MiB over 100 whole-ERA reads (memory kept by the allocator); rows are decoded line by line instead |

## Equivalence with load_provision

`tests/test_store.py`, marked `data`, run with `LEGAL_DATA_ROOT` set and the store in
`artefacts/store` (or `STORE_DIR`):

```
LEGAL_DATA_ROOT=~/Code/legal-rag-router/data uv run pytest tests/test_store.py -m data -s
```

It draws 200 files from each of `ukpga`, regnal-year `ukpga`, `uksi`, `wsi` and `nisi` with a
fixed seed, and from each takes the instrument, up to 3 random provisions, a top-level
provision and up to 3 coordinates with several runs. Result: 3,131 coordinates, 0 mismatches
(compared with `==` and by `repr`, so key order and types match), 64 of them with several runs,
and 209 unknown coordinates that raise `KeyError` from both. It also checks every one of the
134,219 instruments: `instrument()` equals the file's first record and `has_text` agrees with
its `structure`.

## Publishing

```
uv run --with huggingface_hub python build/publish_dataset.py --data "$LEGAL_DATA_ROOT"
uv run --with huggingface_hub python build/publish_dataset.py --data "$LEGAL_DATA_ROOT" --repo OWNER/NAME --yes
```

The first command is a dry run (the default): it checks the store against its manifest and the
router `index/` and `concepts/` against theirs, checks that all three carry the same snapshot,
and prints the upload plan and the OGL v3.0 dataset card. A real upload needs both `--yes` and
`--repo` and sends everything in one commit. Dataset layout, as `deploy/fetch_artefacts.py`
reads it:

| Path in the dataset | Contents |
|---|---|
| `corpus-manifest.json` and the store files | The text store, at the root |
| `index/index-manifest.json` and the files it lists | The router index |
| `concepts/concepts-manifest.json` and the files it lists | The router concept index |
| `symspell/symspell-manifest.json` and the files it lists | The SymSpell spelling dictionary |
| `dense/dense-manifest.json` and the files it lists | The dense index |
| `models/models-files-manifest.json` and the files it lists | The query model |
| `clarify/clarify-manifest.json` and the files it lists | The clarify ranking prior |
| `pit/pit-manifest.json` and the files it lists | Verified point-in-time statutory limits |
| `README.md` | The dataset card |
