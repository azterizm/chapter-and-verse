# Worker steering (applies to every spec)

- Repo: `.`. Read `docs/ARCHITECTURE.md` for the module map and `engine/chapterverse/contracts.py` for shared types. Do not edit `contracts.py` or `config.py`.
- Edit only the files your spec's design lists. Other workers edit other files at the same time.
- Mark each task `[x]` in your `tasks.md` when it is done.
- Checks before finishing, on the whole repo: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q`. All must pass. If a failure is in a file you do not own, say so in your summary and do not edit that file.
- Never run git. Never deploy, upload, or run `modal`, `wrangler`, `hf` or `huggingface-cli`. Never add or remove dependencies.
- Keys are read from the environment at call time only (`GROQ_API_KEY`, `CLOUDFLARE_AI_WORKER_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`). Never print, log or write them. Never read `~/Code/legal-rag-verifier/.router_key`.
- Never write inside `~/Code/legal-rag-router` or `~/Code/legal-rag-verifier` (read only).
- Scratch: `$CV_SCRATCH/<spec name>/`.
- Doctrine: stability, then accuracy, then speed, then cost. A change that makes a wrong sentence pass is never acceptable.
- Text people read (reports, notices): cold, plain, short. No em or en dashes, arrows, curly quotes, or the signs ≥ ≈ ×.
- End with a summary of under 200 words: what changed, test results, numbers measured, open issues.
