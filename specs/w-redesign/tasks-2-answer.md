# W2 Answer: tasks (parallel with W3 and W4)

Files you own: `web/src/components/Turn.tsx`, `Citation.tsx`, `RefusalCard.tsx`, `Receipt.tsx`, new `QuoteBlock.tsx`, `StatusLine.tsx`, `ActionRow.tsx`, `Notice.tsx`, the `// W2 answer strings` block of `web/src/text.ts` (append only inside it), new `web/test/dom/answer.test.tsx`. Do not edit App.tsx, QueryBox, Footer, SourcePanel, StatusBadge, WindowText (other workers own them), nor `web/src/api/*`, `web/src/state/*`.

Contract with the other workers: `Turn` keeps its current props and adds none except optional `onNewQuestion?: () => void`; citation clicks keep calling the existing handler App passes today (read App.tsx to see it). Sources are rendered by W3's components; Turn must not render the source panel itself if App renders it (read App.tsx; keep the current split).

Allowed shell commands: exactly as in tasks-1-foundation.md.

- [ ] 1. Read `specs/w-redesign/requirements.md` (R2, R4, R5, R7), `design.md` (sections 3, 5, 6, 7), the files you own, `web/src/components/ui/*`, `web/src/index.css`, `web/src/lib/*`.
- [ ] 2. Restyle Turn with tokens and Tailwind classes only: question line, StatusLine, sentences with sentence-in motion and chips, muted abstention, Notice lines, QuoteBlock for `quote_requested` extractive answers (and the same block, without per-block copy, for other extractive reasons with their notice), refusal/clarify/error cards (Retry on error re-sends as today).
- [ ] 3. ActionRow after done: Copy (R4 text format, toast), Verify (existing receipt logic, inline result), New question.
- [ ] 4. Tests: sentence chips render engine labels; abstention has no chips; Copy produces the exact R4 text for a fixture turn; quote blocks render full text and copy buttons; action row appears only when done; reduced-motion class presence is not required.
- [ ] 5. Run the four npm scripts. Print a short summary.
