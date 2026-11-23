# Dense Retrieval Model Bake-off Report

Dev split only (112 hand rows). Test split is sealed.

Notice: Dense-only within a pool is an optimistic estimate. Model choice uses RRF on dev.

## Retrieval Performance Table

| Ranker | hit@1 | hit@5 | hit@10 | MRR |
|---|---|---|---|---|
| BM25F alone | 0.679 | 0.839 | 0.911 | 0.752 |
| bge-small-en-v1.5 (dense-only) | 0.562 | 0.866 | 0.902 | 0.682 |
| bge-small-en-v1.5 (RRF) | 0.670 | 0.902 | 0.946 | 0.768 |
| e5-small-v2 (dense-only) | 0.518 | 0.821 | 0.884 | 0.639 |
| e5-small-v2 (RRF) | 0.589 | 0.884 | 0.938 | 0.716 |
| gte-small (dense-only) | 0.482 | 0.812 | 0.893 | 0.618 |
| gte-small (RRF) | 0.643 | 0.911 | 0.938 | 0.737 |

## Throughput and Sizing

| Model | Throughput (docs/s) | Mac CPU Threads | Mean Tokens/Doc | Dimension |
|---|---|---|---|---|
| bge-small-en-v1.5 | about 40 (Mac CPU) | 4 | 211.7 | 384 |
| e5-small-v2 | about 40 (Mac CPU) | 4 | 213.4 | 384 |
| gte-small | Modal L4 (pool build) | n/a | 211.7 | 384 |

## Configuration and Notes

- Prefix fields used: instrument: title, year, other_titles; provision: crossheading, number_label, title
- Pool size: 34781 documents (30000 distractors sampled with seed 20261122)
- Recommended model by dev RRF MRR: bge-small-en-v1.5
- Pool vectors for bge-small-en-v1.5 and e5-small-v2 were embedded on the Mac CPU; gte-small pool vectors came from the Modal L4 build. Query vectors were embedded on CPU for every model, as production will.

