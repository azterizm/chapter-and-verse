# W Redesign: design

## 1. Principles
- One colour. Everything is ink (navy-tinted neutrals) plus one blue. No second hue anywhere: status, warnings and highlights are expressed with the blue, with weight, with icons, or with muted ink.
- The content leads. The answer and the statute text are the largest, calmest things on screen. Chrome is quiet, small and grouped.
- Few type sizes, two weights. If something needs emphasis, use space or position before size.
- Motion explains change (something arrived, something opened, this is the source you tapped). Never decorative loops except the status shimmer while work is happening.

## 2. Tokens (`web/src/index.css`, Tailwind v4 `@theme inline`, shadcn variable names)

Light (`:root`):
| Token | Value | Use |
|---|---|---|
| --background | #F6F6FC | page |
| --surface | #FFFFFF | cards, composer, source cards |
| --foreground | #000040 | text |
| --muted-foreground | #5B5B85 | secondary text, captions |
| --border | #E3E3F1 | hairlines (1px) |
| --primary | #0001E6 | the one colour: send button, chips text, links, focus ring, highlight underline |
| --primary-foreground | #FFFFFF | text on primary |
| --primary-soft | #EBEBFF | chip background, highlight background, hover |
| --ring | #0001E6 | focus ring (2px, offset 2px) |

Dark (`.dark`, and `@media (prefers-color-scheme: dark)` when the theme is System):
| Token | Value |
|---|---|
| --background | #06061A |
| --surface | #0E0E27 |
| --foreground | #EEEEFF |
| --muted-foreground | #9C9CC4 |
| --border | #222244 |
| --primary | #8585FF |
| --primary-foreground | #06061A |
| --primary-soft | #18184A |
| --ring | #8585FF |

Check every pair for contrast in a test (R6) and adjust the lightness only if a pair fails; keep the hue.

Radius: --radius 14px (cards), chips and composer fully rounded (9999px). Shadow: one only, `0 1px 2px rgb(0 0 64 / 0.06), 0 8px 24px rgb(0 0 64 / 0.06)` for the composer and the expanded source card; none in dark (use border instead).

## 3. Type
One family: Satoshi, self-hosted (`/fonts/Satoshi-Medium.woff2` 500, `/fonts/Satoshi-Bold.woff2` 700), `font-display: swap`, preload Medium in `index.html` (a `<link rel="preload">` tag, not a script). Fallback `ui-sans-serif, -apple-system, system-ui, sans-serif`.
Four sizes only:
| Name | Size / line height | Weight | Use |
|---|---|---|---|
| display | 32 / 38, tracking -0.02em | 700 | wordmark only |
| body | 17 / 27 | 500 | answer sentences, statute text, composer |
| ui | 15 / 22 | 500 (700 for card titles) | buttons, chips, card titles, source labels |
| caption | 13 / 18 | 500 | status line, notices, footer, timings |
Numbers in citations use `font-variant-numeric: tabular-nums`.

## 4. Layout
- Page: centred column, max width 680 px for the conversation; 16 px side gutter on phones, 24 px from 640 px.
- >= 1024 px: two columns for the active answer: conversation 680 px, sources column 360 px, 32 px gap, sources column `position: sticky; top: 24px; max-height: calc(100dvh - 48px)`, scrolls on its own. Earlier answers keep their sources inline under them (collapsed list).
- < 1024 px: sources are a section under each answer.
- Composer docked at the bottom (`position: sticky; bottom: 0`) inside the column, over a gradient fade from transparent to --background (24 px), safe-area padding (`env(safe-area-inset-bottom)`). Turnstile widget sits directly above the composer, left-aligned, only while a check is needed (TurnstileGate decides).
- Masthead: shown large (display) only in the empty state; once a question exists it becomes a small sticky top bar (ui size wordmark left, theme control is NOT here, it is in the footer), with a hairline that appears only after scrolling.

## 5. Components (shadcn/ui style, files under `web/src/components/ui/` for primitives)
- Button (cva: variants primary, ghost, outline; sizes sm 32, md 44, icon 44): primary is the filled blue circle for send; everything else ghost.
- Chip: citation chip, ui size, height 24, padding 0 8, primary-soft background, primary text, tabular numbers, press state scale 0.97. Selected (its source open): primary background, primary-foreground text.
- SourceCard (Radix Collapsible, `forceMount` content, height animated): header row (label in ui 700, status icon + word in caption muted), two-line preview when closed; open: full text in body size with the highlight (primary-soft background, 2px primary underline), version date caption, actions (Copy, legislation.gov.uk link with external icon).
- Status: In force: no badge (default, say nothing). Not yet in force: outline pill "Not in force". Repealed: muted text with strike-through label and pill "Repealed". Text not held / history not held: pill "Text not held". All pills monochrome (outline in --muted-foreground).
- QuoteBlock: left rule 3px --primary, statute text body size, label above in ui 700, copy icon button top right.
- StatusLine: caption size, muted, with a 1.6 s shimmer gradient across the text while active; words from text.ts: "Finding the law", "Reading", "Checking each point", then removed when the first sentence arrives (after that, a 3-dot pulse at the end of the last sentence until done).
- Notice: caption line, 16 px lucide icon (Info, CircleAlert), muted text.
- ActionRow: ghost icon buttons with labels on wide screens, icons only below 400 px with tooltips (Radix Tooltip) and aria-labels: Copy (Copy icon morphs to Check), Verify (ShieldCheck), New question (Plus, focuses the composer).
- Toast: one at a time, bottom centre above the composer, caption size, surface with border, enters with fade + 8px rise, leaves after 1.6 s.
- Card (refusal, clarify, error): surface, border, radius, 16 px padding, title ui 700, one sentence body muted, chips or a Retry button.

## 6. Motion (CSS only, tokens in index.css)
| Token | Value |
|---|---|
| --ease-out | cubic-bezier(0.22, 1, 0.36, 1) |
| --ease-spring | cubic-bezier(0.32, 0.72, 0, 1) |
| --dur-fast | 150ms |
| --dur-base | 240ms |
| --dur-slow | 420ms |
- sentence-in: opacity 0 to 1, translateY 6px to 0, blur 2px to 0, --dur-base --ease-out.
- card-open: height via `grid-template-rows: 0fr` to `1fr` and opacity, --dur-slow --ease-spring.
- chip-press: scale 0.97, --dur-fast.
- jump-to-source: smooth scroll, then a single highlight pulse on the cited span (background from primary-soft at 100% to 60%, --dur-slow, twice).
- copy-morph: icon cross-fade + scale 0.8 to 1, --dur-fast.
- masthead: the empty-state wordmark shrinks into the top bar on the first question (transform, --dur-slow --ease-spring).
- `@media (prefers-reduced-motion: reduce)`: all durations 0.01ms, no blur, no shimmer, scrolling instant.

## 7. Copy (all in `web/src/text.ts`)
Tone: plain, short, no exclamation marks, no "please", no em dashes.
- Purpose line: "UK law, answered with the section it comes from."
- Composer placeholder: "Ask about UK legislation"
- Send aria-label: "Send"
- Examples (3): "How long do I have to sue in tort?", "How long is the short-term right to reject?", "Quote section 172(1) of the Companies Act 2006"
- Status: "Finding the law", "Reading", "Checking each point"
- Sources heading: "Sources"; card link: "legislation.gov.uk"; copy toast: "Copied"; copy failure: "Copy failed"
- Actions: "Copy", "Verify", "New question"; verify results: "Receipt verified", "Receipt does not match"
- Footer (one line, caption, muted, wraps on phones): "Not legal advice" · "Do not enter personal data" · "Contains public sector information licensed under the Open Government Licence v3.0" · "Law as at {snapshot}" · theme control.
Existing engine-provided messages (refusals, notices) are shown as sent; titles for them stay in text.ts and may be shortened to 6 words or fewer.

## 8. Files
- New: `web/src/index.css` (Tailwind import, tw-animate-css import, tokens, @font-face, base, motion utilities), `web/src/lib/utils.ts` (`cn`), `web/src/lib/theme.ts`, `web/src/lib/clipboard.ts`, `web/src/components/ui/{button,chip,tooltip,collapsible,toast}.tsx`, `web/components.json` (shadcn new-york, cssVariables, lucide, aliases `@/components`, `@/lib`).
- Replaced: `web/src/styles.css` is deleted at the end (after every component moved to tokens).
- Path alias `@/` to `web/src/` in `vite.config.ts` and `tsconfig.json`.
- Tests: vitest `environment: node` stays for existing tests; new DOM tests in `web/test/dom/*.test.tsx` with `// @vitest-environment jsdom` per file and `@testing-library/react`.
