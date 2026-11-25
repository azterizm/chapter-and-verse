# Sprint 5 gates

Branch `sprint-5` from main d5ded21. Code gates at 0f0522d unless stated.

## F12: Groq capacity

### Measured (no provider calls, `eval/token_budget.py`, legal battery dev)

| Call | Tokens, median (chars / 4) | Premise share |
|---|---|---|
| Generate (first block) | 1,308 | 74% |
| Judge, all candidates (today) | 1,545 | |
| Judge, cited windows only | 358 | |
| Judge, cited sections | 420 | |

Real Groq counts are close to chars / 4: on 16 first generate calls, actual / estimate median 1.04, max 1.105. A dev answer averages about 2 generate and 3 judge calls, about 6,900 tokens, so Groq's 8,000 a minute holds about 1.2 answers a minute when it serves both purposes. Daily limits bind as well: Groq 200,000 tokens a day (about 29 answers), Cloudflare 9,500 neurons (about 32 answers).

### Judge on fewer passages: both variants fail the sealed held-out rule, both off

| Detector (Modal, held-out 346 rows) | Caught | False rollback, pass rows | Report |
|---|---|---|---|
| All candidates (20 Nov, production) | 154/155 | 14/191 | `heldout-qwen-modal.md` |
| Cited windows | 155/155 | 24/191 | `heldout-qwen-modal-cited.md` |
| Cited sections | 155/155 | 18/191 | `heldout-qwen-modal-section.md` |

Pre-registered rule for each: caught at least 154/155 and false rollbacks at most 14/191. Both fail on false rollbacks. The judge needs context beyond the cited passage; also, the Modal judge is not deterministic between runs (8 of 261 byte-identical prompts got a different label), so a future attempt needs a same-day control run. fresh2 (151-unit judge gate) cannot test these scopes (no excerpt coordinate). Production policy hash unchanged: 235ec4a6...

### Shipped

- Ledger admission margin 1.5 to 1.2 (measured above). Reservations are still replaced by actual usage after each call.

## Legal battery (S5.2)

80 rows (L41 to L80 added: SIs, carve-outs, quotes, multi-instrument, "not in the Act"); test split re-sealed (44e07076...), run once at 0f0522d.

| Split | Rows passed | must_reach recall |
|---|---|---|
| dev, dense | 38/40 (old 20/20, new 18/20: L43 WTR reg 5, L67 LA s.33) | 0.956 |
| test (sealed, once) | 38/40 (old 20/20, new 18/20: L54 ERA s.155, L80 two Acts) | 0.947 |

Answer level, dev, local engine, Groq and Cloudflare only, one question every 20 s (`eval/legal_answers.py`, reports under `reports/legal-answers/`):

| Run | Rows passed | Incomplete (providers) |
|---|---|---|
| main code (506ece9) | 17/23 | 2 (L65, L73) |
| judge on cited windows (4b47845, not shipped) | 19/23 | 1 |
| judge on Cloudflare first, generate on Groq (0f0522d, ledger 1.2) | 20/23 | 3 (L51, L59, L67); elapsed p50 9.4 s vs 5.3 s |

Judge on Cloudflare first frees Groq for generation (about 2.7 answers a minute on Groq on paper) but Cloudflare's slow calls cost more than they save: more incomplete answers and slower answers. Production keeps Groq first for both purposes.

## F12 gate: not met this sprint

The paced-run gate (no provider-limit incomplete answer at one question every 20 s) is not met by any shipped change: the judge-input changes failed their sealed gates and the provider-order change was worse. Pass counts between single runs vary by a few rows (judge and generation sampling), so they are not compared as improvements. What remains for a later sprint: develop judge-context changes on non-sealed data with a same-day control; Modal warm-up when Groq's window is near full (needs a spend decision); a larger Groq tier.

## Clarify ranking (S5.3)

Optional prior (`CLARIFY_PRIOR_PATH`, off when unset). Sealed concept test, pre-registered (`sprint5-clarify-sealed.md`): hand hit@10 0.912 to 0.934, MRR 0.698 to 0.719, safety identical. Everyday battery dev (dense): hit@1 0.45 to 0.70, MRR 0.600 to 0.758.

## Unchanged safety

| Gate | Result |
|---|---|
| Router battery, 2,112 rows, pipeline | wrong instrument 0/1,897, bound on invented 0/68; diffs against the sealed reference identical to main d5ded21 |
| Security probes, local | 29/29 |
| Judge gate replay (fresh2) | unchanged, 138/151 |
| pytest | 1,261 passed, 7 skipped |
| Web | 158 tests, build ok |

## Web (S5.4)

Engine-recorded mock streams (`web/mock/record_engine.py`), local real-engine preview (`npm run dev:engine` with `scripts/dev_engine.py`, 127.0.0.1 only), `eval/web_smoke.py` in the repo, clarify Change flow tested at phone width.

## Live (deployed 25 Nov 2026 06:55 UTC: A1 engine at sprint-5 4ddd19a, dataset 85bc3efc; Worker unchanged 7b93ed49)

- Dataset revision 85bc3efc adds only `clarify/` (every other manifest byte-identical to 12e38978, checked before upload). Host `engine.env` HF_REVISION bumped (backup `engine.env.bak-pre-s5`).
- Engine healthy 30 s after start; dense on; clarify prior on (snapshot 2026-09-28).
- Live probes at pace 7: 29/29.
- Live legal answers (rubric): L01, L07, L09, L45, L73 pass, 5/5.
- Live clarify for "How long do I have to sue in tort?": s.2 Limitation Act 1980 first (was TULRCA s.22 and s.219 first without dense).
- The full paced live run of the battery is left for after 00:00 UTC: today's local runs used about half of Groq's 200,000-token day on the shared key.
