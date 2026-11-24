# W9 Conversation state: tasks (one worker)
Fix review-4-state.md S1 to S8 and the mock. Files you own: `web/src/state/reducer.ts`, `web/src/App.tsx`, `web/src/components/Turn.tsx`, `RefusalCard.tsx`, `ActionRow.tsx`, `web/src/text.ts` (W2 and W4 blocks), `web/mock/plugin.ts`, `web/mock/streams/clarify-tort.sse` (new), `web/test/reducer.test.ts`, `web/test/mock.test.ts`, `web/test/dom/answer.test.tsx`, `web/test/dom/shell.test.tsx`, new `web/test/dom/choice.test.tsx`. Do not touch `web/src/api/*`. Allowed shell commands: exactly as in tasks-1-foundation.md.
- [ ] 1. Read review-4-state.md, design.md, the files you own.
- [ ] 2. S5 reducer and its tests.
- [ ] 3. S1, S2, S6, S7 in App and Turn (and RefusalCard), S3, S4.
- [ ] 4. Mock clarify stream and routing; DOM tests in choice.test.tsx: choose replaces in place with the original heading and the "Using ..." line; sent body query is the composed text; no coordinate text anywhere in the DOM; clarify turn has no action row; one divider between two turns; Change reopens the list and re-choosing replaces again without nested brackets; Retry replaces.
- [ ] 5. S8 audit. Run the four npm scripts. Print a summary with the S8 list.
