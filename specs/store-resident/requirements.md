# Resident store: requirements

The engine on the Oracle A1 VM (24 GB RAM) uses 126 MiB at idle because the text store is memory-mapped and read on demand. Oracle reclaims an Always Free A1 instance that stays under 20% CPU, network and memory (95th percentile) for 7 days. Holding about 5 GB of useful data resident keeps memory over 20% of 24 GB (4.8 GB) and also removes zstd decoding from every request.

- R1 `chapterverse.store` SHALL offer a resident mode, selected by env `STORE_RESIDENT=1`: at open, every frame of `text.zst` is decompressed once into memory (a single `bytes` buffer or an array of per-frame buffers plus offsets), and `get()` serves from it with no decompression. Results SHALL be byte-identical to the mapped mode for every coordinate (the existing equivalence tests run in both modes).
- R2 Open time and RSS in resident mode SHALL be measured on this Mac with the real store (`artefacts/store`) and reported: load seconds, RSS after load, `get()` p50/p99 for `uk/ukpga/1996/18/s124` and over 2,000 seeded coordinates, against the mapped mode.
- R3 The resident buffer SHALL be loaded once per process and shared by all request threads (read-only, thread-safe).
- R4 If decompressed size would exceed `STORE_RESIDENT_MAX_BYTES` (env, default 8 GiB), the store SHALL stay mapped and log one warning.
- R5 Add `STORE_RESIDENT=1` and `STORE_RESIDENT_MAX_BYTES` to `deploy/engine.env.example` is NOT yours; instead write the two lines in your summary so the orchestrator adds them.
- R6 Tests on the fixture store in both modes; the real-store measurement is a script under `$CV_SCRATCH/store-resident/`.
