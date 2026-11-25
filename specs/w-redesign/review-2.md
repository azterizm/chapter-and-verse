# Visual review 2 (screenshots of ba45934)

Good: Satoshi everywhere, 680 + 360 px layout, short chips, provision-first source titles, Details panel, icon theme control, quiet status line.

Fix:
1. Quote answers (`extractive` with reason `quote_requested`): one heading for the group (the common provision label, for example "Companies Act 2006, section 172(1)", derived from the windows' labels; if they do not share a parent, no group heading), then each block labelled only by its own short label ("(a)", or "s.172(1)(a)" when there is no group heading), statute text, and per-block copy; one legislation.gov.uk link per block as an icon button with aria-label, not a text link under every block. Do not render the Sources column or section for a quote answer (the quote is the source); keep the windows in the DOM only if jump-to-source needs them (it does not for quotes).
2. Empty state: the wordmark, purpose line, examples and the composer form one group, vertically centred in the viewport (slightly above centre, about 40 percent from the top) on every size; on the first question the composer moves to its docked position at the bottom (--dur-slow --ease-spring transform) and the hero fades out.
3. Footer on narrow screens (< 640 px): each legal line on its own line with no separator dots; theme control on its own row. From 640 px: one line with dots as now.
