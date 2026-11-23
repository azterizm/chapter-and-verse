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

(Filled in after the sealed run.)
