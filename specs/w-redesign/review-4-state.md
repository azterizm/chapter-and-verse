# Review 4: conversation state (user report, live, 24 Nov 2026)

User: asked "How long do I have to sue in tort?". The "Choose a provision" card appeared; they chose the first option. The card stayed open. Then a Details button and two horizontal lines appeared, then a new heading "uk/ukpga/1980/58/s2" and an answer.

Causes (orchestrator, read from the code):
- `Turn.tsx` clarify chips and `RefusalCard.tsx` suggestion chips call `onAsk(c.coordinate)`: the raw coordinate is sent as a NEW question, appended as a new turn whose heading is the coordinate, and the user's question is lost (the engine answers "s.2" in general, not the question).
- The clarify turn is left as it was (card open) and, being `done` with a receipt, shows the action row and Details although it holds no answer.
- Two dividers: `App.tsx` wraps each turn in a div with `border-b` and `Turn.tsx` `<article>` also has `border-b`.
- `Retry` on an error card also appends a duplicate turn with the same heading.

Measured (engine, 24 Nov): re-asking the original question with the candidate's citation appended in brackets, the citation being the candidate label before ": " (for example "How long do I have to sue in tort? (s.2 Limitation Act 1980)"), binds exactly the chosen coordinate for 120 of 120 candidates across 12 citation-less questions. The `context` request field does not bind (it only resolves references). So no engine change is needed.

## Requirements (EARS)

S1 Choose replaces in place. WHEN the user chooses a candidate on a clarify card (or a suggestion on a refusal card) THE SYSTEM SHALL replace that turn in place (same position in the transcript, the old turn removed, any stream of it already finished) with a new turn that:
- sends `query = "<original question> (<citation>)"` where citation is the candidate label up to the first ": " (the whole label when there is none); the original question is the turn's own question (`turn.question`, see S5), never a previously composed query, so choosing twice never nests brackets;
- displays as its heading the original question only;
- shows under the heading one caption line: "Using <citation>" and a small text button "Change" (only when the turn keeps the alternatives, S2);
- never displays a coordinate string anywhere.
S2 Change. The replacing turn keeps the clarify candidates (labels and coordinates) as `alternatives`. WHEN the user activates "Change" THE SYSTEM SHALL show the same choice list inline (chips, the current one marked selected and disabled); choosing another replaces the turn in place again (S1). Escape or a second "Change" closes the list.
S3 Answer-only actions. THE SYSTEM SHALL show the action row (Copy, Download evidence, Details) only on a turn that has at least one answer sentence or an extractive answer, and only when the turn is done. A clarify-only, refusal-only or error turn shows no action row and no Details.
S4 One divider. Exactly one divider between consecutive turns and none after the last turn: remove the turn `border-b` from one of the two places (keep the App wrapper rule with `last:border-b-0`).
S5 State model. In `web/src/state/reducer.ts`: `Turn` gets `question` (what the heading shows) separate from `query` (what was sent), and optional `choice: { citation, alternatives } | null`; a new action `replace` (`{ type: "replace"; oldTurnId; turnId; query; question; choice }`) that swaps the turn at oldTurnId's index for a new streaming turn and makes it active; `start` keeps working for typed questions (question = query). Retry on an error card uses `replace` with the same query, question and choice (no duplicate heading). Reducer tests for: replace keeps position and removes the old turn; choosing twice keeps the original question and does not nest brackets; retry replaces; reset clears everything; an event for a replaced (gone) turn id is ignored.
S6 Streams. A replaced turn's stream, if somehow still open, is aborted before the replacement starts (the app already aborts the previous request when a new one starts: reuse it). The Turnstile gate flow is unchanged: a choice is an ask and needs a token exactly as a typed question does.
S7 Sources and focus. After a replacement the sources column shows the new turn's sources; focus moves to the new turn's heading region (not the composer) so screen readers hear the answer; scroll keeps the turn in view.
S8 Audit. Search the web code for every other place a coordinate or a raw query could be shown or sent as a new question (`onAsk(` calls, `.coordinate` rendered as text) and list them in the summary with what you did.

## Mock for local checks
Add `web/mock/streams/clarify-tort.sse` (a clarify event for "How long do I have to sue in tort?" with three candidates whose labels follow the engine format, for example "s.2 Limitation Act 1980: Time limit for actions founded on tort", plus route, receipt, done; copy shapes from `clarify.sse`) and route queries containing "sue in tort" WITHOUT "(" to it; queries containing "(s.2 Limitation Act 1980)" route to the normal answer stream. Update the mock routing test.
