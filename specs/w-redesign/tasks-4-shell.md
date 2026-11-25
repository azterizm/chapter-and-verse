# W4 Shell: tasks (parallel with W2 and W3)

Files you own: `web/src/App.tsx`, `web/src/components/QueryBox.tsx`, `Footer.tsx`, `Turnstile.tsx` (markup and classes ONLY, never the gate logic), the `// W4 shell strings` block of `web/src/text.ts` (append only inside it), new `web/test/dom/shell.test.tsx`. Do not edit Turn, Citation, SourcePanel, StatusBadge, WindowText, `web/src/api/*`, `web/src/state/*`, `web/src/turnstileText.ts` logic.

Allowed shell commands: exactly as in tasks-1-foundation.md.

- [x] 1. Read `specs/w-redesign/requirements.md` (R1, R3 layout, R6, R7, R8), `design.md` (sections 3 to 7), the files you own, `web/src/components/ui/*`, `web/src/index.css`, `web/src/lib/*`.
- [x] 2. Empty state (wordmark display size, purpose line, three example chips that fill the composer), top bar after the first question with the wordmark transition, two-column layout at >= 1024 px with the sticky sources column for the active answer, inline sources under earlier answers (pass the same props SourcePanel takes today).
- [x] 3. Composer: docked pill, auto-grow to 6 lines, Enter sends, Shift+Enter newline, disabled send when empty or while streaming, circular primary send button with ArrowUp icon, safe-area padding, fade above it; Turnstile widget placement above the composer.
- [x] 4. Footer one line with the legal lines and the System/Light/Dark control wired to `lib/theme.ts`; toast provider mounted once.
- [x] 5. Tests: empty state shows exactly the three examples; example fills and focuses without sending; Enter sends, Shift+Enter does not; theme control switches the `dark` class and persists; footer legal lines present.
- [x] 6. Run the four npm scripts. Print a short summary.
