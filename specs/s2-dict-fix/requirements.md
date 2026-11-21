# Spelling dictionary fix: requirements

The Slice 2 discovery gate failed through the app: hand-written held-out concept queries hit@10 0.857 and MRR 0.615 against the gate's 0.88 and 0.63, while the router alone reproduces the sealed 0.879 and 0.630. Cause: `build/symspell_dict.py` builds the SymSpell dictionary from the concept index vocabulary, whose terms are STEMMED ("becaus", "unfairl", "automaticall"). Correct English words absent from that stemmed list are "corrected" to stems or other inflections. 21 of 91 hand queries were changed, all wrongly, including a meaning flip:

dismissed to dismisses (x5); automatically to automaticall; unfairly to unfairl; because to becaus; puts to put; unwanted to unwant; violating to violation; arrested to arrester; defrauding to defraying; induced to induce; refused to refuse; **unmarried to married**.

- R1 Build the dictionary from surface word forms, not stems: every word in the text of the store's records (`chapterverse.store` over `artefacts/store`: provision `text`, `text_after`, `title`, `crossheading`, and instrument `title` and `long_title`), lower-cased, letters and internal hyphens or apostrophes only, with counts. Keep the builder deterministic and report term count, file size, build time and peak RSS. Keep the output path and manifest format.
- R2 Safety rules in `normalise.py`, applied after SymSpell picks a suggestion:
  - never apply a correction when the original word is in the dictionary;
  - never apply a correction when original and suggestion share the same first 5 letters and differ only in the ending (inflection or derivation), unless the original has no dictionary entry and the edit distance is 1 and the word is 6 or more letters;
  - never apply a correction that adds or removes a negating prefix (un, in, im, il, ir, non, dis, mis) relative to the original;
  - never correct capitalised words in the middle of a sentence (names are protected by the integration step too; this is a second guard).
- R3 Regression: run normalise over all 153 test-split queries and the 91 hand queries of `~/Code/legal-rag-router/batteries/concept/uk.jsonl`; report how many are changed and list every change. Target: 0 changes on the hand queries (they are correctly spelled); every remaining change justified in the report. Real typos must still be fixed: "unfiar dismisal compensatory award cap", "Employmnet Rights Act 1996 section 98", "redundnacy pay" (tests).
- R4 Re-run the discovery gate: `uv run python -m eval.discovery_gate --out $CV_SCRATCH/s2-gates/disc2` and report hand hit@10 and MRR through the pipeline against the gate (0.88, 0.63).
