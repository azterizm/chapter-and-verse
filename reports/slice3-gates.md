# Slice 3 gates: dense hybrid retrieval

## Pre-registration (written before the sealed run)

Date: 23 Nov 2026. Branch `slice-3`. This section is committed before the sealed test split is scored, and is not edited afterwards.

### Candidate

Discovery for questions without a citation: BM25F top 50 (router `discover`) fused with dense top 50 by reciprocal rank fusion (k = 60), top 10 shown as chips. Dense: `bge-small-en-v1.5` (ONNX, CPU at query time), 1,322,050 concept documents with an embedding-only prefix, int8 vectors with binary sign codes, exact search (Hamming pass keeping 1,000, int8 rescoring). Index `artefacts/dense/index` (545 MB, deterministic from the Modal build of 23 Nov 2026).

Choices made on the dev split only (112 hand rows):

- Model: bge-small-en-v1.5, by dev RRF MRR in a pool bake-off against e5-small-v2 and gte-small (`reports/slice3-bakeoff-dev.md`).
- Fusion: dev pipeline hit@10 0.902 to 0.929, MRR 0.739 to 0.750.
- ColBERT rerank (`answerai-colbert-small-v1`, top 24, subsection windows): rejected on dev. hit@10 0.866 and MRR 0.635 (below fusion and below BM25F alone), latency p50 1.35 s, p95 2.06 s on the Mac. It is not evaluated on the test split and stays off.

### Method

One run of `uv run python -m eval.discovery_gate --split test --sealed-test --dense-dir artefacts/dense/index --models-dir artefacts/models` on the sealed concept battery (`batteries/concept/uk.jsonl`, test split: 91 hand rows, 50 appendix B negatives, 12 user probes), seal checked by the gate. The same run scores the pipeline without dense (Slice 2) side by side. No change to code, parameters or index between this commit and the run. The run is not repeated.

### Gate (all must hold)

1. Accuracy: hand rows with dense, hit@10 above 0.879 and MRR above 0.630 (Slice 2 sealed figures).
2. Safety identical to Slice 2:
   - phantom candidates 0 and route status mismatches none;
   - user probe rows (fictional instruments) still refused, 12 of 12;
   - appendix B negatives: the confident share unchanged (confident is set by BM25F alone);
   - rag-security-probes 29/29 with dense on (local run on 23 Nov: 29/29);
   - "no provision matched" decided by BM25F alone (by construction; tested).
3. Latency on the A1 (live host): dense stage (query embedding plus search) p95 at or below 100 ms, measured before any deploy.

If gate 1 or 2 fails, Slice 3 dense is not deployed. Gate 3 is measured on the A1 after this run and before any deploy.

## Results

Sealed run: 23 Nov 2026, from a clean git worktree at the pre-registration commit `acee1e9` (no code, parameter or index change), run once. Seal checked: `d34ce21d...` (battery and index v2 seal). 153 test rows.

### Gate 1, accuracy (hand rows, 91)

| Pipeline | hit@1 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| Slice 2 (BM25F, no dense) | 0.505 | 0.802 | 0.879 | 0.630 |
| Slice 3 (BM25F + dense, RRF) | 0.593 | 0.813 | 0.912 | 0.695 |

hit@10 above 0.879 and MRR above 0.630: **pass**.

### Gate 2, safety

| Check | Slice 2 | Slice 3 |
|---|---|---|
| Phantom candidates | 0 | 0 |
| Route status mismatches | none | none |
| User probe rows refused (fictional instruments) | 12 of 12 | 12 of 12 |
| Appendix B negatives, confident share | 0.28 | 0.28 |
| rag-security-probes, local, dense on | 29/29 | 29/29 |

**Pass.**

### Gate 3, latency on the A1

Mac figures from the sealed run (dense stage): query embedding p50 2.6 ms, p95 3.5 ms; search p50 20.1 ms, p95 24.5 ms.

A1 (23 Nov 2026, 06:54 UTC, live host with the engine running): a throwaway container on the engine's base image (`python:3.12-slim-bookworm`, onnxruntime 1.32.0), bge-small-en-v1.5 at the pinned revision, exact search over arrays of the full index shape (1,322,050 x 384 int8 plus 48-byte codes; search cost depends on shape only), the 112 dev questions:

| Threads | Embed p50 / p95 | Search p50 / p95 | Dense stage p50 / p95 |
|---|---|---|---|
| 4 | 10.4 / 11.9 ms | 55.3 / 59.6 ms | 65.7 / 70.4 ms |
| 2 | 16.0 / 18.7 ms | 54.8 / 59.2 ms | 71.0 / 76.7 ms |

p95 at or below 100 ms: **pass**. Container and image removed afterwards.

## Decision

All three gates pass. Dense RRF is adopted for discovery; ColBERT is not. Deploy follows the user's approval (publish the index and model to the dataset, then deploy with dense on).
