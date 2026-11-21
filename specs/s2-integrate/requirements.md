# Slice 2 integration: requirements

Components are built and committed: `engine/chapterverse/normalise.py` (SymSpell, d527e17), `nlp.py` and `pii.py` (spaCy pass and PII mask, d425830), `discovery.py` (did-you-mean chips, negative evidence, out-of-scope rule, c6aef53). Wire them into the request path in `engine/chapterverse/pipeline.py` and `app.py`.

## R1 Order per request
1. Protected spans of the raw query: citation and identifier spans (as `normalise` computes them).
2. `nlp.analyse(raw, protected)`; take the entity spans PII would mask (PERSON, ORG, FAC with company suffix, and the R5 proper-noun fallback names).
3. `normalise(raw, dictionary, protect=citation spans + name spans)`. Add the `protect` parameter to `normalise.py`; protected words are never corrected. Test: "Can Priya Nkemelu claim redundancy pay from Zentrix Ltd?" yields no corrections (found in review: without protection "Priya" became "Prima" and "Zentrix" became "Zentrum").
4. Route the normalised text. Send `corrections_applied` as a `NoticeEvent` when there are corrections, listing them ("Read unfiar as unfair, dismisal as dismissal.").
5. On `ROUTE_UNRESOLVED`: the out-of-scope rule, then `discover_step`: `Suggest` becomes a clarify event with the discovered candidates; `NoEvidence` becomes the negative-evidence refusal; out of scope becomes the `out_of_scope` refusal. Never bind an undiscovered or unconfirmed candidate.
6. Before ANY provider call (generation prompt and judge calls), the user's question text in the request SHALL be the PII-masked text. Passages are statute text and are not masked. Add an instruction line to the generation prompt that placeholders like [PERSON 1] stand for people the user named and must be kept as written; `PROMPT_SHA256` changes, record old and new.
7. Streamed sentences shown to the user replace placeholders with the original names from the in-memory reverse map for this request (the verifier checks the masked sentence; the user reads the unmasked one). The receipt stores only the masked sentence text and `query_sha256` of the raw query, never names.
8. Never log raw or masked query text, names or corrections; log counts only.

## R2 Startup
Load the SymSpell dictionary once at startup: from `SYMSPELL_DICT` if set, else build it in memory from the concept index with `build/symspell_dict.py`'s builder logic (moved into an importable function under `engine/chapterverse/` if needed; keep the script working). Load the spaCy model once at startup. Report startup time added.

## R3 Tests (end to end through the app with the fixture router, store, fake rotation and fake judge)
- "unfiar dismisal compensatory award cap": corrections notice, then a clarify event with discovered candidates (fixture concept index), no provider call.
- Gibberish: negative-evidence refusal, no provider call.
- "What does US federal law say about overtime?": out_of_scope refusal.
- "Can Priya Nkemelu claim under s.124 ERA 1996?": every request the fake rotation and fake judge receive contains no "Priya" and no "Nkemelu"; the streamed sentence the client receives shows the name if the fake model wrote the placeholder.
- Cited questions behave exactly as before (existing tests pass unchanged except where the new notice adds an event; event order stays valid).
- No name or query text in captured logs.

## R4 Checks
Steering checks; `npm run test` in `web` (the web page already renders clarify, refusal and notice events; change web only if a test shows a gap).
