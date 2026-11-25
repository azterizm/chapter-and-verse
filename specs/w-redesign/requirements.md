# W Redesign: requirements

The site works (Sprint 4 live). Now it must be ready for everyone: beautiful, minimal, calm, and obviously trustworthy. It should belong to memonsystems (monochrome, one colour, Satoshi) without copying memonsystems.com. UX follows Apple's Human Interface Guidelines: clarity, deference (the law and the answer are the content, the interface steps back), depth through motion and layering, not decoration.

Read `design.md` in this folder before any task. It is the source of truth for tokens, type, layout, components, motion and copy.

## User stories and acceptance criteria (EARS)

R1 Ask. As anyone, I can ask a question about UK law from a phone or a desktop.
- WHEN the page loads with no history THE SYSTEM SHALL show the wordmark, one short line of purpose, three example questions and the composer, and nothing else above the fold.
- WHEN the user taps an example THE SYSTEM SHALL put it in the composer and focus it (not send it).
- WHILE the composer is empty THE SYSTEM SHALL disable the send button; Enter sends, Shift+Enter adds a line; the composer grows to 6 lines then scrolls.
- WHERE Turnstile is configured THE SYSTEM SHALL keep its widget visible next to the composer, as today (`TurnstileGate` logic unchanged).

R2 Read the answer. As a reader, I see the answer arrive calmly and can tell at a glance what is verified and where each point comes from.
- WHILE an answer streams THE SYSTEM SHALL show one quiet status line (design.md "Status") instead of a spinner, and reveal each sentence with the "sentence-in" motion.
- WHEN a sentence has citations THE SYSTEM SHALL render them as compact chips after the sentence, labelled exactly as the engine renders them (`s.2 Limitation Act 1980`).
- WHEN a sentence is the abstention (verdict `refused`) THE SYSTEM SHALL render it in the muted style with no chips.
- WHEN the answer is extractive with reason `quote_requested` THE SYSTEM SHALL render each provision as a quotation block (label, full text, link) with its own copy button.
- WHEN a notice arrives THE SYSTEM SHALL render it as one short caption line with an icon, never a box.

R3 Sources. As a careful reader, I can check every citation against the provision text in one tap, without losing my place.
- Each answer SHALL carry its own sources (the `sources` event windows) as a list of source cards, collapsed to one line each (label, status, a two-line preview).
- WHEN the user taps a citation chip THE SYSTEM SHALL expand that source card, scroll it into view smoothly (respecting reduced motion), highlight the cited span, and move focus to it; Escape or a second tap collapses it.
- An expanded card SHALL show the full window text with the highlight, the status, the version date and a "legislation.gov.uk" link, plus a copy button for the provision text with its citation.
- On wide screens (>= 1024 px) the active answer's sources SHALL sit in a sticky column beside the answer; on phones they sit under the answer. Cards SHALL stay mounted (collapse by height, never unmount), so anchors and jump-to-source keep working.

R4 Copy and keep. As a user, I can take the answer with me.
- WHEN an answer is done THE SYSTEM SHALL show an action row: Copy, Verify (receipt), and New question.
- Copy SHALL put plain text on the clipboard: the sentences in order, each followed by its citation labels in brackets, then a "Sources" list of label and URL, then the answer id. The button morphs to a check mark for 1.5 s and a toast says "Copied". If the Clipboard API fails, select the text in a hidden textarea and use `document.execCommand("copy")`; if that fails, the toast says "Copy failed".
- Verify keeps today's receipt behaviour (download the fact JSON, SHA-256 checked in the browser) and shows the result inline as a small check or cross.

R5 Refusals, clarify, errors. As a user who asked something the site cannot answer, I am told plainly and offered a way forward.
- Refusal and clarify render as one card each: a title of at most 6 words, one sentence, and choices as chips (clarify candidates send a follow-up as today).
- Errors (network, stream fault) render as one card with a Retry button.

R6 Theme, motion, access.
- Light and dark themes from the tokens in design.md; default follows the system; a three-way control (System, Light, Dark) in the footer, remembered in localStorage (wrapped in try/catch), applied by an external module, never an inline script (CSP).
- All motion uses the tokens in design.md and is removed under `prefers-reduced-motion: reduce`.
- WCAG 2.2 AA: text contrast >= 4.5:1, UI >= 3:1, focus ring visible on every control, touch targets >= 44 px, `aria-live="polite"` on the answer, landmarks, labels on icon buttons, logical tab order.
- Works at 320 px width with no horizontal scroll.

R7 Minimal copy. Every user-facing string lives in `web/src/text.ts` (no inline strings in components). Copy follows design.md "Copy". Keep the legal lines: not legal advice, do not enter personal data, Open Government Licence attribution, snapshot date.

R8 Keep working (do not change): the SSE client, parser, order validator and reducer logic (`web/src/api/*`, `web/src/state/*` behaviour), receipt raw-bytes hashing, Turnstile gate logic, the CSP in `deploy/pages/_headers` (the build must not need any change to it), no third-party requests other than Turnstile, no analytics, no fingerprinting.

R9 Quality gates: `npm run --prefix web typecheck`, `lint`, `test` (existing tests pass, new DOM tests per task), `build`; bundle JS gzip under 110 kB, CSS gzip under 20 kB; no console errors.
