# Route raw first; fix overlapping spans: requirements

The Slice 2 rerun of the router battery through the pipeline (`uv run python -m eval.run`) kept safety (0 wrong instrument of 1,897, 0 invented bound of 68) but showed:

1. 9 faults: `ValueError at _retokenize.pyx:57` in `engine/chapterverse/nlp.py` when protected spans overlap (for example a citation span and an identifier span over the same text). The request ends in `error` before `route`. Rows: uk-misroute-0054, 0067, 0265, 0293, 0461, 0465, 0567, 0653, uk-identifier-0027 (queries in `~/Code/legal-rag-router/batteries/uk/*.jsonl`; one is the injection-shaped `uk/ukpga/1996/18' or '1'=='1`).
2. 2 regressions where spelling correction damaged a citation the router could already bind: uk-misroute-0753 ("Over-arching" in an SI title: "arching" corrected to "arcing") and uk-informal-0009 ("tulrca s.188": "tulrca" corrected to "tulr").
3. 3 improvements to keep: uk-typo-0045, 0046, 0047 (typo'd titles now bind correctly after correction).

- R1 `nlp.analyse`: merge overlapping and touching protected spans into their union before `retokenize.merge`; never pass overlapping spans; if retokenizing still raises, continue without merging (log a count, never the text). Test with the 9 queries: no exception.
- R2 Pipeline routing order in `pipeline.py`: route the RAW query first. If the raw result is `ROUTE_BOUNDED`, use it and the raw text; spelling correction is not applied and no corrections notice is sent. Otherwise normalise (with protected spans as today) and route the normalised text; use the normalised result only if it is `ROUTE_BOUNDED`, or if the raw result was `ROUTE_UNRESOLVED` (then discovery runs on the normalised text); in every other case keep the raw result (raw abstentions and clarifications stand). Deterministic; no extra provider calls.
- R3 Tests: the two regression queries bind as the sealed run does; the three typo rows still bind after correction; a fully raw-bound query sends no corrections notice; uncited misspelled queries still go to discovery with corrections.
- R4 Re-run `uv run python -m eval.run --out $CV_SCRATCH/s2-gates/final/battery2` and report: faults (target 0), and every row difference against the sealed run, classified as expected (clarify candidates now provision-level), improvement (typo rows) or regression (target 0).
