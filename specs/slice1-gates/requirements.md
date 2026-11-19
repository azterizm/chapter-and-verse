# Slice 1 gates: requirements

Context: Chapter & Verse answers UK legislation questions with sentence-verified, system-rendered citations. The engine is built (`engine/chapterverse/`, see `docs/ARCHITECTURE.md` §10 and §11). Gate 1 (router battery through the app) is already run; its outputs are in `SCRATCH/c2b/battery-none/` (`summary.json`, `metrics.json`, `differences.json`, `faults.json`). This spec covers the remaining Slice 1 gates and the gates report. Plan: the project plan §9 Slice 1 and §11.

`SCRATCH` = `$CV_SCRATCH`.

## R1 Latency gate on 20 hand-written questions

User story: as the operator, I want measured latency and answer quality on realistic questions, so that Slice 1's latency gate has numbers from the real corpus and real providers.

- R1.1 WHEN the question file is written THEN `batteries/slice1_questions.jsonl` SHALL hold 20 rows with fields `id`, `query`, `expected_kind`, `notes`, covering: ERA 1996 s.124 and at least 4 other ERA Part X provisions; at least 3 SIs; explicit citations only (no follow-ups); one title-only Act; one invented Act; one repealed provision; one prospective provision; one ambiguous citation; one query over 4,096 bytes.
- R1.2 WHEN the gate runs THEN it SHALL send each question over HTTP to a local uvicorn of `chapterverse.app:app` started with `LEGAL_DATA_ROOT=~/Code/legal-rag-router/data`, `STORE_DIR=artefacts/store`, `GEN_PROVIDERS=groq,cloudflare`, `JUDGE_PROVIDERS=groq,cloudflare`.
- R1.3 The gate SHALL send at most 150 Groq and 40 Cloudflare requests in total, and SHALL stop early if Groq's `x-ratelimit-remaining-requests` header falls below 300 (a sealed battery run shares the Groq quota).
- R1.4 The gate SHALL record per question: event types in order, time to first source, time to first verified sentence, total time, sentences emitted and refused, rollbacks, written-citation failures, provider calls by provider; and SHALL report p50 and p95 of the three times.
- R1.5 Every stream SHALL pass `chapterverse.contracts.validate_order`, and every receipt's sha256 SHALL equal its `answer_id`.
- R1.6 The worker SHALL read every generated answer against the source text shown in its `sources` event and SHALL list, quoting the sentence: any sentence that is wrong against the source; any sentence made vague by the rule that bans model-written section numbers (example: "certain specified grounds" where the source lists ss.100 to 105).

## R2 Smoke script

User story: as the operator, I want one command that checks a deployed URL, so that each deploy is verified the same way.

- R2.1 `eval/smoke.py --base-url URL` SHALL send the four Slice 1 "done when" questions: "What is the limit of the compensatory award under s.124 ERA 1996?"; "What does section 3 of the Marchwood Commercial Arbitration Act 1996 say?"; a title-only Act (`uk/ukpga/1980/15`, National Health Service (Invalid Direction) Act 1980); and the s.124 question again.
- R2.2 It SHALL check: event order with `validate_order`; s.124 is `ROUTE_BOUNDED` with at least one supported sentence or an extractive answer; Marchwood is `EPISTEMIC_ABSTENTION_INSTRUMENT_NOT_FOUND`; the title-only Act gives a `TEXT_NOT_HELD` refusal with a link; every citation URL starts with `https://www.legislation.gov.uk/` and its path matches its coordinate (offline check, no fetch); the receipt sha256 equals `answer_id`.
- R2.3 It SHALL exit 0 when every check passes and 1 otherwise, printing one line per check.
- R2.4 `tests/test_eval_smoke.py` SHALL test the checks offline against recorded streams (no network).

## R3 Boot time and memory

- R3.1 The gate SHALL measure, on this Mac: seconds from process start to the first healthy `GET /api/health`; RSS after start; RSS after the 20 questions. Every number SHALL be labelled "M4 Mac, not the A1".

## R4 Gates report

- R4.1 `reports/slice1-gates.md` SHALL have one section per gate (router battery, latency, smoke, boot and RAM) with numbers and pass or fail. The router battery section SHALL use the numbers in `SCRATCH/c2b/battery-none/summary.json` (pipeline vs direct vs sealed; 0 row differences; 0 pipeline faults).
- R4.2 The report SHALL leave out the judge gate (another worker writes `reports/judge-gate-2026-11.md`) and SHALL link to it.
- R4.3 Voice: cold, plain, short, facts only. No em or en dashes, arrows, curly quotes, or the signs ≥ ≈ ×. No "**Label:** text" lead-ins. Tables are fine.
