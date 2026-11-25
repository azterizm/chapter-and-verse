# S5.1d Ledger margin calibrated to measured Groq token counts: requirements

The ledger estimates prompt tokens as characters x 1.5 / 4 (`TOKENS_PER_CHAR` in `engine/chapterverse/providers/ledger.py`) and admits a Groq call only if that estimate plus the output reserve fits the minute window. Measured 25 Nov on 16 first generate calls (legal battery dev, real Groq usage vs characters / 4): ratio median 1.04, max 1.105. The 1.5 margin keeps calls off Groq that would fit; after the call the reservation is replaced by actual usage anyway.

- R1 Set the margin to 1.2 (`TOKENS_PER_CHAR = 1.2 / 4`), with the measurement in the docstring (date, n = 16, median 1.04, max 1.105, source `reports/legal-answers/2026-11-25-base-506ece9-dev.json` and `eval/token_budget.py`). Update the module docstring and `docs/ARCHITECTURE.md` where they state 1.5.
- R2 Nothing else changes: reservation, release, header handling, window wait, budgets. The Cloudflare neuron estimate uses the same function: say in the docstring that its daily budget check becomes 20% less conservative and that the 9,500 of 10,000 budget keeps a 5% reserve.
- R3 Tests: the estimate for a known message list; existing ledger tests pass (update only values that encode 1.5, list each). No network.
