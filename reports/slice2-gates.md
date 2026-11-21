# Slice 2 Gate Results

Slice 2 answers questions that cite no provision. It adds spelling correction against the statute vocabulary, a spaCy pass, masking of personal details before any model provider sees the question, and discovery that offers candidate provisions for the user to choose; it never binds an unconfirmed candidate. The raw question is routed first, and spelling correction applies only when it does not bind. Gates run on 2026-11-21 on branch slice-2, on the real corpus, with a fake model rotation and no LLM calls.

## Gate 1 Concept Battery

| Metric | Pipeline | Direct | Sealed | Plan Target |
|---|---|---|---|---|
| Hand rows hit@1 (91 rows) | 0.505 | 0.505 | 0.505 | N/A |
| Hand rows hit@5 (91 rows) | 0.802 | 0.802 | 0.802 | N/A |
| Hand rows hit@10 (91 rows) | 0.879 (80/91) | 0.879 | 0.879 | 88.0% |
| Hand rows MRR (91 rows) | 0.630 | 0.630 | 0.630 | 0.63 |
| All test rows hit@10 (153 rows) | 0.784 | 0.853 | N/A | N/A |
| Phantom candidates | 0 | N/A | N/A | 0 |
| Route status mismatches | 0 | N/A | N/A | 0 |
| Normalise changes on hand rows | 0 of 91 | N/A | N/A | 0 |
| Normalise changes on test rows | 3 of 153 | N/A | N/A | 0 |
| Refused user probe rows | 12 of 12 | N/A | N/A | 12 |
| Pipeline discovery latency p50 | 68 ms | N/A | N/A | N/A |

MRR passes at 0.630; hit@10 is 87.9%, which equals the sealed router figure but is 0.1 points under the 88.0% plan line.

## Gate 2 Security Probes

| Probe Category | Total | Applicable | Passed | Refused | Not Applicable |
|---|---|---|---|---|---|
| Mode A no_upload fictional Acts | 6 | 6 | 6 | 6 | 0 |
| Mode C chimera titles | 3 | 3 | 3 | 3 | 0 |
| Mode C out-of-bounds section | 1 | 1 | 1 | 1 | 0 |
| Mode C devolution mismatch | 1 | 1 | 1 | 1 | 0 |
| Mode C repealed provision | 1 | 1 | 1 | 1 | 0 |
| Mode A upload tenant documents | 17 | 0 | 0 | 0 | 17 |
| Total probes | 29 | 12 | 12 | 12 | 17 |

Pass; 12 of 12 applicable probes pass, and 17 upload probes are not applicable.

## Gate 3 Router Battery Pipeline

| Metric | Pipeline Result | Sealed Run |
|---|---|---|
| Total evaluated rows | 2112 | 2112 |
| Wrong instrument | 0 of 1897 | 0 of 1897 |
| Invented law bound | 0 of 68 | 0 of 68 |
| False abstentions | 82 of 1897 | 82 of 1897 |
| Pipeline faults | 0 | 0 |
| Row differences against sealed run | 17 | 0 |
| Regressions | 0 | 0 |
| Provision-level candidates replacing clarify | 14 | 0 |
| Typo rows bound after spelling correction | 3 | 0 |

Pass; 0 faults, 0 regressions, 0 wrong instruments, and 0 invented laws bound across 2,112 rows.

## Fixes found by the gates

- The SymSpell dictionary was rebuilt from 340,447 surface words of statute text because the previous dictionary built from stemmed index terms changed 21 of 91 correctly spelled queries, including unmarried to married.
- Discovery candidate count was raised from 8 to 10 to match gate measurement.
- Capitalised words mid-sentence are not corrected and the raw question is routed first, preventing a refusal quoting "Blackberr" for "Blackmere".
- Retokenizer faults in spaCy caused by ValueError on overlapping protected spans were resolved by merging spans (9 faults fixed).
- Regressions where spelling correction damaged bindable citations (Over-arching, tulrca) were resolved by routing the raw question first and correcting only when it does not bind (2 regressions fixed).
