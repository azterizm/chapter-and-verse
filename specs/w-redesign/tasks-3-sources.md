# W3 Sources: tasks (parallel with W2 and W4)

Files you own: `web/src/components/SourcePanel.tsx`, `StatusBadge.tsx`, `WindowText.tsx`, new `SourceCard.tsx`, the `// W3 source strings` block of `web/src/text.ts` (append only inside it), new `web/test/dom/sources.test.tsx`. Do not edit App.tsx, Turn.tsx, Citation.tsx, QueryBox, Footer, nor `web/src/api/*`, `web/src/state/*`.

Contract: keep `SourcePanel`'s current props and the element ids/anchors App uses to jump to a window (read App.tsx `showSource`); add optional props only. Cards stay mounted when collapsed.

Allowed shell commands: exactly as in tasks-1-foundation.md.

- [x] 1. Read `specs/w-redesign/requirements.md` (R3, R6, R7), `design.md` (sections 2, 4, 5, 6, 7), the files you own, App.tsx (read only), `web/src/components/ui/*`, `web/src/index.css`, `web/src/lib/*`.
- [x] 2. SourceCard: collapsed one-line header plus two-line preview; open shows full text with highlight, status, version date, Copy (provision text plus citation label plus URL) and the legislation.gov.uk link; keyboard: Enter/Space toggles, Escape collapses; aria-expanded and aria-controls.
- [x] 3. SourcePanel as a list of SourceCards; opening from a citation (the existing jump path) expands the card, scrolls smoothly, focuses it, and pulses the highlight; StatusBadge per design.md (monochrome, say nothing for in force); WindowText highlight styling.
- [x] 4. Tests: jump-to-source expands and focuses the right card; collapsed cards remain in the DOM; status labels; copy text format; Escape collapses.
- [x] 5. Run the four npm scripts. Print a short summary.
